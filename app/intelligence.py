from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(os.getenv("VINTED_HISTORY_DB", "/app/data/vinted-history.sqlite3"))
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at REAL NOT NULL UNIQUE,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS listing_observations (
            sync_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
            listing_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT,
            price_cents INTEGER,
            currency TEXT,
            favourites INTEGER,
            views INTEGER,
            listed_at TEXT,
            vinted_url TEXT,
            PRIMARY KEY (sync_id, listing_id)
        );
        CREATE INDEX IF NOT EXISTS idx_listing_obs_item
            ON listing_observations(listing_id, sync_id);

        CREATE TABLE IF NOT EXISTS profile_observations (
            sync_id INTEGER PRIMARY KEY REFERENCES sync_runs(id) ON DELETE CASCADE,
            followers INTEGER,
            following INTEGER
        );

        CREATE TABLE IF NOT EXISTS favorite_events (
            notification_id TEXT PRIMARY KEY,
            item_id TEXT,
            item_title TEXT,
            actor TEXT,
            occurred_at TEXT,
            first_seen_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_favorite_events_item
            ON favorite_events(item_id, first_seen_at);

        CREATE TABLE IF NOT EXISTS orders_history (
            order_key TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            title TEXT NOT NULL,
            counterparty TEXT,
            total_cents INTEGER,
            currency TEXT,
            status TEXT,
            lifecycle_status TEXT,
            is_closed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            vinted_url TEXT,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orders_direction
            ON orders_history(direction, updated_at);

        CREATE TABLE IF NOT EXISTS market_research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT NOT NULL,
            query TEXT NOT NULL,
            requested_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            completed_at REAL,
            sample_count INTEGER,
            min_cents INTEGER,
            median_cents INTEGER,
            max_cents INTEGER,
            currency TEXT,
            results_json TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_market_listing
            ON market_research(listing_id, requested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_market_queue
            ON market_research(status, requested_at);
        """
    )


def _int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _iso_year(value: Any) -> int | None:
    timestamp = _parse_time(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).year


def _void_order(row: sqlite3.Row | dict[str, Any]) -> bool:
    if isinstance(row, sqlite3.Row):
        values = [row[key] if key in row.keys() else None for key in ("status", "lifecycle_status")]
    else:
        values = [row.get(key) for key in ("status", "lifecycle_status")]
    lower = " ".join(str(value or "") for value in values).lower()
    return any(word in lower for word in ("cancel", "refund", "failed"))


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    stop = {"the", "a", "an", "and", "or", "of", "by", "book", "books", "novel", "edition"}
    return {word for word in words if len(word) > 1 and word not in stop}


def _similarity(a: str, b: str) -> float:
    left = _tokens(a)
    right = _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def record_snapshot(snapshot: dict[str, Any]) -> None:
    collected_at = float(snapshot.get("collected_at") or time.time())
    listings = list(snapshot.get("listings") or [])
    notifications = list(snapshot.get("notifications") or [])
    orders = list(snapshot.get("orders") or [])
    market_results = list(snapshot.get("market_results") or [])

    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sync_runs(collected_at, created_at) VALUES(?, ?)",
            (collected_at, time.time()),
        )
        run = conn.execute(
            "SELECT id FROM sync_runs WHERE collected_at = ?", (collected_at,)
        ).fetchone()
        if not run:
            return
        sync_id = int(run["id"])

        current_user = snapshot.get("current_user") or {}
        conn.execute(
            """
            INSERT OR REPLACE INTO profile_observations(
                sync_id, followers, following
            ) VALUES(?, ?, ?)
            """,
            (
                sync_id,
                _int(current_user.get("followers_count")),
                _int(current_user.get("following_count")),
            ),
        )

        for item in listings:
            listing_id = str(item.get("id") or "")
            if not listing_id:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO listing_observations(
                    sync_id, listing_id, title, status, price_cents, currency,
                    favourites, views, listed_at, vinted_url
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sync_id,
                    listing_id,
                    str(item.get("title") or "Untitled"),
                    item.get("status"),
                    _int(item.get("price_cents")),
                    item.get("currency"),
                    _int(item.get("favourites")),
                    _int(item.get("views")),
                    item.get("listed_at"),
                    item.get("vinted_url"),
                ),
            )

        for notification in notifications:
            if notification.get("category") != "favorite":
                continue
            notification_id = str(notification.get("id") or "")
            if not notification_id:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO favorite_events(
                    notification_id, item_id, item_title, actor,
                    occurred_at, first_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    str(notification.get("item_id") or "") or None,
                    notification.get("item_title"),
                    notification.get("actor"),
                    notification.get("occurred_at"),
                    collected_at,
                ),
            )

        for order in orders:
            direction = str(order.get("direction") or "")
            identity = str(order.get("id") or order.get("thread_id") or "")
            if direction not in {"sell", "buy"} or not identity:
                continue
            key = f"{direction}:{identity}"
            conn.execute(
                """
                INSERT INTO orders_history(
                    order_key, direction, title, counterparty, total_cents,
                    currency, status, lifecycle_status, is_closed, updated_at,
                    vinted_url, first_seen_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_key) DO UPDATE SET
                    title=excluded.title,
                    counterparty=excluded.counterparty,
                    total_cents=excluded.total_cents,
                    currency=excluded.currency,
                    status=excluded.status,
                    lifecycle_status=excluded.lifecycle_status,
                    is_closed=excluded.is_closed,
                    updated_at=excluded.updated_at,
                    vinted_url=excluded.vinted_url,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    key,
                    direction,
                    str(order.get("title") or "Vinted order"),
                    order.get("counterparty"),
                    _int(order.get("total_cents")),
                    order.get("currency"),
                    order.get("status"),
                    order.get("lifecycle_status"),
                    1 if order.get("is_closed") else 0,
                    order.get("updated_at"),
                    order.get("vinted_url"),
                    collected_at,
                    collected_at,
                ),
            )

        for result in market_results:
            _complete_market_result(conn, result, collected_at)


def _complete_market_result(
    conn: sqlite3.Connection, result: dict[str, Any], completed_at: float
) -> None:
    job_id = _int(result.get("job_id"))
    if not job_id:
        return
    job = conn.execute(
        "SELECT listing_id, query FROM market_research WHERE id = ?", (job_id,)
    ).fetchone()
    if not job:
        return

    error = str(result.get("error") or "").strip() or None
    raw_results = [
        row for row in (result.get("results") or []) if isinstance(row, dict)
    ]
    filtered: list[dict[str, Any]] = []
    prices: list[int] = []
    currency = "EUR"

    for row in raw_results:
        title = str(row.get("title") or "")
        if _similarity(str(job["query"]), title) < 0.22:
            continue
        price = _int(row.get("price_cents"))
        if price is None or price <= 0:
            continue
        compact = {
            "id": str(row.get("id") or ""),
            "title": title,
            "price_cents": price,
            "currency": str(row.get("currency") or "EUR"),
            "url": row.get("url"),
        }
        currency = compact["currency"]
        prices.append(price)
        filtered.append(compact)

    prices.sort()
    median = int(statistics.median(prices)) if prices else None
    conn.execute(
        """
        UPDATE market_research
        SET status=?, completed_at=?, sample_count=?, min_cents=?,
            median_cents=?, max_cents=?, currency=?, results_json=?, error=?
        WHERE id=?
        """,
        (
            "error" if error else "completed",
            completed_at,
            len(prices),
            min(prices) if prices else None,
            median,
            max(prices) if prices else None,
            currency,
            json.dumps(filtered[:30], ensure_ascii=False),
            error,
            job_id,
        ),
    )


def request_market_research(listing_id: str, title: str) -> dict[str, Any]:
    listing_id = str(listing_id or "").strip()
    title = str(title or "").strip()
    if not listing_id or not title:
        raise ValueError("listing_id and title are required")

    with _lock, _connect() as conn:
        existing = conn.execute(
            """
            SELECT * FROM market_research
            WHERE listing_id=? AND status='queued'
            ORDER BY requested_at DESC LIMIT 1
            """,
            (listing_id,),
        ).fetchone()
        if existing:
            return dict(existing)

        conn.execute(
            """
            INSERT INTO market_research(listing_id, query, requested_at, status)
            VALUES(?, ?, ?, 'queued')
            """,
            (listing_id, title, time.time()),
        )
        row = conn.execute(
            "SELECT * FROM market_research WHERE id=last_insert_rowid()"
        ).fetchone()
        return dict(row) if row else {}


def queued_market_jobs(limit: int = 3) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, listing_id, query, requested_at
            FROM market_research
            WHERE status='queued'
            ORDER BY requested_at ASC LIMIT ?
            """,
            (max(1, min(int(limit), 5)),),
        ).fetchall()
        return [dict(row) for row in rows]


def _market_for_listing(conn: sqlite3.Connection, listing_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, requested_at, completed_at, sample_count, min_cents,
               median_cents, max_cents, currency, results_json, error
        FROM market_research
        WHERE listing_id=? AND status IN ('completed', 'error')
        ORDER BY completed_at DESC LIMIT 1
        """,
        (listing_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["results"] = json.loads(result.pop("results_json") or "[]")
    except json.JSONDecodeError:
        result["results"] = []
    return result


def _history_for_listing(
    conn: sqlite3.Connection,
    listing_id: str,
    now: float,
) -> dict[str, Any]:
    first = conn.execute(
        """
        SELECT sr.collected_at, lo.favourites, lo.views, lo.price_cents, lo.status
        FROM listing_observations lo
        JOIN sync_runs sr ON sr.id=lo.sync_id
        WHERE lo.listing_id=?
        ORDER BY sr.collected_at ASC LIMIT 1
        """,
        (listing_id,),
    ).fetchone()
    week = conn.execute(
        """
        SELECT sr.collected_at, lo.favourites, lo.views, lo.price_cents, lo.status
        FROM listing_observations lo
        JOIN sync_runs sr ON sr.id=lo.sync_id
        WHERE lo.listing_id=? AND sr.collected_at<=?
        ORDER BY sr.collected_at DESC LIMIT 1
        """,
        (listing_id, now - 7 * 86400),
    ).fetchone()
    month = conn.execute(
        """
        SELECT sr.collected_at, lo.favourites, lo.views, lo.price_cents, lo.status
        FROM listing_observations lo
        JOIN sync_runs sr ON sr.id=lo.sync_id
        WHERE lo.listing_id=? AND sr.collected_at<=?
        ORDER BY sr.collected_at DESC LIMIT 1
        """,
        (listing_id, now - 30 * 86400),
    ).fetchone()
    return {
        "first": dict(first) if first else None,
        "week": dict(week) if week else None,
        "month": dict(month) if month else None,
    }


def _age_days(item: dict[str, Any], history: dict[str, Any], now: float) -> int:
    started = _parse_time(item.get("listed_at"))
    if started is None and history.get("first"):
        started = float(history["first"]["collected_at"])
    if started is None:
        return 0
    return max(0, int((now - started) // 86400))


def _delta(current: Any, previous: dict[str, Any] | None, key: str) -> int | None:
    current_value = _int(current)
    previous_value = _int((previous or {}).get(key))
    if current_value is None or previous_value is None:
        return None
    return current_value - previous_value


def _sales_analytics(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
    current_year = datetime.fromtimestamp(now, tz=timezone.utc).year
    rows = conn.execute(
        "SELECT * FROM orders_history ORDER BY updated_at DESC"
    ).fetchall()
    sales = [row for row in rows if row["direction"] == "sell" and not _void_order(row)]
    purchases = [row for row in rows if row["direction"] == "buy" and not _void_order(row)]
    ytd_sales = [row for row in sales if _iso_year(row["updated_at"]) == current_year]
    ytd_buys = [row for row in purchases if _iso_year(row["updated_at"]) == current_year]

    sale_values = [
        int(row["total_cents"]) for row in ytd_sales if row["total_cents"] is not None
    ]
    buy_values = [
        int(row["total_cents"]) for row in ytd_buys if row["total_cents"] is not None
    ]

    monthly: dict[str, dict[str, int]] = {}
    for row in ytd_sales:
        timestamp = _parse_time(row["updated_at"])
        if timestamp is None:
            continue
        month = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m")
        bucket = monthly.setdefault(month, {"count": 0, "cents": 0})
        bucket["count"] += 1
        bucket["cents"] += int(row["total_cents"] or 0)

    sell_times = conn.execute(
        """
        SELECT listing_id,
               MIN(CASE WHEN status='active' THEN sr.collected_at END) AS active_at,
               MIN(CASE WHEN status='sold' THEN sr.collected_at END) AS sold_at
        FROM listing_observations lo
        JOIN sync_runs sr ON sr.id=lo.sync_id
        GROUP BY listing_id
        HAVING active_at IS NOT NULL AND sold_at IS NOT NULL AND sold_at>=active_at
        """
    ).fetchall()
    days_to_sell = [
        (float(row["sold_at"]) - float(row["active_at"])) / 86400
        for row in sell_times
        if row["active_at"] is not None and row["sold_at"] is not None
    ]

    return {
        "year": current_year,
        "sales_count": len(ytd_sales),
        "sales_cents": sum(sale_values),
        "average_sale_cents": round(statistics.mean(sale_values)) if sale_values else None,
        "median_sale_cents": round(statistics.median(sale_values)) if sale_values else None,
        "completed_sales": sum(1 for row in ytd_sales if row["is_closed"]),
        "purchase_count": len(ytd_buys),
        "purchase_cents": sum(buy_values),
        "net_cashflow_cents": sum(sale_values) - sum(buy_values),
        "median_days_to_sell": round(statistics.median(days_to_sell), 1)
        if days_to_sell
        else None,
        "monthly": [
            {"month": month, **values}
            for month, values in sorted(monthly.items())
        ],
    }


def _favorite_analytics(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
    week = now - 7 * 86400
    month = now - 30 * 86400
    last7 = conn.execute(
        "SELECT COUNT(*) AS n FROM favorite_events WHERE first_seen_at>=?", (week,)
    ).fetchone()["n"]
    last30 = conn.execute(
        "SELECT COUNT(*) AS n FROM favorite_events WHERE first_seen_at>=?", (month,)
    ).fetchone()["n"]
    top = conn.execute(
        """
        SELECT item_id, COALESCE(MAX(item_title), 'Listing') AS title,
               COUNT(*) AS events, MAX(first_seen_at) AS last_event_at
        FROM favorite_events
        WHERE first_seen_at>=?
        GROUP BY item_id
        ORDER BY events DESC, last_event_at DESC
        LIMIT 10
        """,
        (month,),
    ).fetchall()
    return {
        "events_7d": int(last7 or 0),
        "events_30d": int(last30 or 0),
        "top_30d": [dict(row) for row in top],
    }


def _daily_last(rows: list[sqlite3.Row], value_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        collected_at = float(row["collected_at"])
        day = datetime.fromtimestamp(collected_at, tz=timezone.utc).strftime("%Y-%m-%d")
        item = {"day": day, "collected_at": collected_at}
        for key in value_keys:
            item[key] = row[key]
        by_day[day] = item
    return [by_day[key] for key in sorted(by_day)]


def _audience_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT sr.collected_at, po.followers, po.following
        FROM profile_observations po
        JOIN sync_runs sr ON sr.id=po.sync_id
        WHERE po.followers IS NOT NULL OR po.following IS NOT NULL
        ORDER BY sr.collected_at ASC
        """
    ).fetchall()
    daily = _daily_last(rows, ("followers", "following"))
    follower_values = [row for row in daily if row.get("followers") is not None]
    current = int(follower_values[-1]["followers"]) if follower_values else None
    first = int(follower_values[0]["followers"]) if follower_values else None
    change_7d = None
    if follower_values and current is not None:
        threshold = float(follower_values[-1]["collected_at"]) - 7 * 86400
        baseline = next(
            (
                row
                for row in reversed(follower_values)
                if float(row["collected_at"]) <= threshold
            ),
            follower_values[0],
        )
        if baseline.get("followers") is not None:
            change_7d = current - int(baseline["followers"])
    return {
        "followers": current,
        "following": next(
            (
                int(row["following"])
                for row in reversed(daily)
                if row.get("following") is not None
            ),
            None,
        ),
        "followers_change": (current - first)
        if current is not None and first is not None
        else None,
        "followers_change_7d": change_7d,
        "daily": daily[-180:],
    }


def _views_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT sr.collected_at, lo.listing_id, lo.title, lo.views, lo.favourites,
               lo.status
        FROM listing_observations lo
        JOIN sync_runs sr ON sr.id=lo.sync_id
        WHERE lo.views IS NOT NULL
        ORDER BY sr.collected_at ASC, lo.listing_id ASC
        """
    ).fetchall()

    previous_by_listing: dict[str, int] = {}
    gained_by_day: dict[str, int] = {}
    active_totals_by_sync: dict[float, int] = {}
    listing_daily: dict[str, dict[str, dict[str, Any]]] = {}
    titles: dict[str, str] = {}

    for row in rows:
        listing_id = str(row["listing_id"])
        views = int(row["views"])
        collected_at = float(row["collected_at"])
        day = datetime.fromtimestamp(collected_at, tz=timezone.utc).strftime("%Y-%m-%d")
        titles[listing_id] = str(row["title"] or "Listing")

        previous = previous_by_listing.get(listing_id)
        if previous is not None and views >= previous:
            gained_by_day[day] = gained_by_day.get(day, 0) + (views - previous)
        previous_by_listing[listing_id] = views

        if str(row["status"] or "").lower() == "active":
            active_totals_by_sync[collected_at] = active_totals_by_sync.get(collected_at, 0) + views

        item = {
            "day": day,
            "collected_at": collected_at,
            "views": views,
            "favourites": _int(row["favourites"]),
        }
        listing_daily.setdefault(listing_id, {})[day] = item

    active_rows = [
        {"collected_at": ts, "views": total}
        for ts, total in sorted(active_totals_by_sync.items())
    ]
    active_daily = _daily_last(
        [
            {
                "collected_at": row["collected_at"],
                "views": row["views"],
            }
            for row in active_rows
        ],
        ("views",),
    ) if active_rows else []

    gained_daily = [
        {"day": day, "views_gained": gained_by_day[day]}
        for day in sorted(gained_by_day)
    ]

    current_listing_rows = []
    for listing_id, days in listing_daily.items():
        series = [days[key] for key in sorted(days)]
        latest = series[-1]
        current_listing_rows.append(
            {
                "listing_id": listing_id,
                "title": titles.get(listing_id) or "Listing",
                "views": latest["views"],
                "favourites": latest.get("favourites"),
                "daily": series[-180:],
            }
        )
    current_listing_rows.sort(
        key=lambda row: (-int(row.get("views") or 0), str(row.get("title") or ""))
    )

    return {
        "daily_views_gained": gained_daily[-180:],
        "daily_active_total": active_daily[-180:],
        "listings": current_listing_rows[:250],
        "total_active_views": active_daily[-1]["views"] if active_daily else None,
        "views_gained_7d": sum(
            int(row["views_gained"])
            for row in gained_daily[-7:]
        ),
    }


def intelligence_payload(current_listings: list[dict[str, Any]]) -> dict[str, Any]:
    now = time.time()
    active = [
        item for item in current_listings
        if str(item.get("status") or "").lower() == "active"
    ]

    with _connect() as conn:
        enriched: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []

        for item in active:
            listing_id = str(item.get("id") or "")
            if not listing_id:
                continue
            history = _history_for_listing(conn, listing_id, now)
            age_days = _age_days(item, history, now)
            favourites = _int(item.get("favourites")) or 0
            views = _int(item.get("views"))
            fav_7d = _delta(favourites, history.get("week"), "favourites")
            views_7d = _delta(views, history.get("week"), "views")
            market = _market_for_listing(conn, listing_id)

            row = {
                **item,
                "age_days": age_days,
                "favourites_7d": fav_7d,
                "views_7d": views_7d,
                "market": market,
            }
            enriched.append(row)

            action = None
            if age_days >= 90 and favourites <= 1:
                action = (
                    100,
                    "Relist or rewrite",
                    f"{age_days} days old with only {favourites} favorite"
                    + ("" if favourites == 1 else "s"),
                )
            elif age_days >= 60 and favourites == 0:
                action = (95, "Relist", f"{age_days} days old with no favorites")
            elif age_days >= 45 and favourites >= 5 and (fav_7d is None or fav_7d <= 1):
                action = (
                    90,
                    "Review price",
                    f"{favourites} favorites but still unsold after {age_days} days",
                )
            elif age_days >= 45 and not market:
                action = (
                    80,
                    "Research market",
                    f"{age_days} days old and no comparable-price check yet",
                )
            elif fav_7d is not None and fav_7d >= 3:
                action = (
                    45,
                    "Leave alone",
                    f"+{fav_7d} favorites in the last 7 days",
                )

            if action:
                priority, name, reason = action
                actions.append(
                    {
                        "listing_id": listing_id,
                        "title": item.get("title"),
                        "priority": priority,
                        "action": name,
                        "reason": reason,
                        "age_days": age_days,
                        "favourites": favourites,
                        "favourites_7d": fav_7d,
                        "price_cents": item.get("price_cents"),
                        "currency": item.get("currency") or "EUR",
                        "vinted_url": item.get("vinted_url"),
                        "market": market,
                    }
                )

        actions.sort(key=lambda row: (-int(row["priority"]), -int(row["age_days"])))
        stale = sorted(
            [row for row in enriched if row["age_days"] >= 30],
            key=lambda row: (-int(row["age_days"]), int(row.get("favourites") or 0)),
        )

        market_recent = conn.execute(
            """
            SELECT id, listing_id, query, requested_at, status, completed_at,
                   sample_count, min_cents, median_cents, max_cents, currency, error
            FROM market_research
            ORDER BY requested_at DESC LIMIT 20
            """
        ).fetchall()
        queued = conn.execute(
            "SELECT COUNT(*) AS n FROM market_research WHERE status='queued'"
        ).fetchone()["n"]

        return {
            "generated_at": now,
            "listing_signals": enriched,
            "today": {
                "actions": actions[:30],
                "count": len(actions),
                "high_priority": sum(1 for row in actions if row["priority"] >= 80),
            },
            "stale": {
                "count_30d": sum(1 for row in enriched if row["age_days"] >= 30),
                "count_60d": sum(1 for row in enriched if row["age_days"] >= 60),
                "count_90d": sum(1 for row in enriched if row["age_days"] >= 90),
                "listings": stale[:100],
            },
            "sales": _sales_analytics(conn, now),
            "favorites": _favorite_analytics(conn, now),
            "audience": _audience_analytics(conn),
            "views": _views_analytics(conn),
            "market": {
                "queued": int(queued or 0),
                "recent": [dict(row) for row in market_recent],
            },
        }
