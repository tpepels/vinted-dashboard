"""Vinted-specific behavioral analytics from already-collected snapshots.

The collector can record a snapshot every few minutes. Analytics does not need
that density, so this module asks the database for one end-of-day point per
listing plus one pre-window baseline. This bounds a 90-day analytics response
at roughly listings * 91 points instead of loading every raw observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import models
from app.constants import Channel, ListingStatus
from app.stock_policy import sale_counts_as_sold


@dataclass(frozen=True)
class SnapshotPoint:
    listing_id: uuid.UUID
    captured_at: datetime
    price_cents: int | None
    status: str | None
    views: int | None
    favourites: int | None


def _point(row: Any) -> SnapshotPoint:
    return SnapshotPoint(
        listing_id=row["channel_listing_id"],
        captured_at=row["captured_at"],
        price_cents=row["price_cents"],
        status=row["status"],
        views=row["views"],
        favourites=row["favourites"],
    )


def daily_snapshot_series(
    session: Session,
    listing_ids: list[uuid.UUID],
    since: datetime,
) -> dict[uuid.UUID, list[SnapshotPoint]]:
    if not listing_ids:
        return {}

    snapshot = models.ListingSnapshot
    baseline_times = (
        select(
            snapshot.channel_listing_id.label("channel_listing_id"),
            func.max(snapshot.captured_at).label("captured_at"),
        )
        .where(
            snapshot.channel_listing_id.in_(listing_ids),
            snapshot.captured_at < since,
        )
        .group_by(snapshot.channel_listing_id)
        .subquery()
    )
    baselines = session.execute(
        select(
            snapshot.channel_listing_id.label("channel_listing_id"),
            snapshot.captured_at.label("captured_at"),
            snapshot.price_cents.label("price_cents"),
            snapshot.status.label("status"),
            snapshot.views.label("views"),
            snapshot.favourites.label("favourites"),
        ).join(
            baseline_times,
            and_(
                snapshot.channel_listing_id == baseline_times.c.channel_listing_id,
                snapshot.captured_at == baseline_times.c.captured_at,
            ),
        )
    ).mappings().all()

    day = func.date(snapshot.captured_at)
    ranked = (
        select(
            snapshot.channel_listing_id.label("channel_listing_id"),
            snapshot.captured_at.label("captured_at"),
            snapshot.price_cents.label("price_cents"),
            snapshot.status.label("status"),
            snapshot.views.label("views"),
            snapshot.favourites.label("favourites"),
            func.row_number()
            .over(
                partition_by=(snapshot.channel_listing_id, day),
                order_by=snapshot.captured_at.desc(),
            )
            .label("rn"),
        )
        .where(
            snapshot.channel_listing_id.in_(listing_ids),
            snapshot.captured_at >= since,
        )
        .subquery()
    )
    daily_rows = session.execute(
        select(
            ranked.c.channel_listing_id,
            ranked.c.captured_at,
            ranked.c.price_cents,
            ranked.c.status,
            ranked.c.views,
            ranked.c.favourites,
        )
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.channel_listing_id, ranked.c.captured_at)
    ).mappings().all()

    result: dict[uuid.UUID, list[SnapshotPoint]] = {}
    for row in baselines:
        point = _point(row)
        result.setdefault(point.listing_id, []).append(point)
    for row in daily_rows:
        point = _point(row)
        result.setdefault(point.listing_id, []).append(point)
    for listing_id in result:
        result[listing_id].sort(key=lambda point: point.captured_at)
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
            if number > 1e11:
                number /= 1000
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def listing_started_at(listing: models.ChannelListing) -> datetime:
    raw = (listing.extra or {}).get("listed_at")
    return _parse_datetime(raw) or listing.first_seen_at


def listing_started_source(listing: models.ChannelListing) -> str:
    raw = (listing.extra or {}).get("listed_at")
    return "vinted" if _parse_datetime(raw) is not None else "first_seen"


def sale_time(sale: models.Sale) -> tuple[datetime | None, str | None]:
    candidates = [
        ("order", sale.occurred_at),
        ("first_seen", sale.first_seen_at),
    ]
    present = [(source, value) for source, value in candidates if value is not None]
    if not present:
        return None, None
    source, value = min(present, key=lambda pair: pair[1])
    return value, source


def _window_baseline(
    series: list[SnapshotPoint],
    cutoff: datetime,
) -> SnapshotPoint | None:
    """Use the first observation inside the requested window.

    Daily downsampling means the nearest point before a cutoff can belong to
    the previous calendar day and materially overstate a 7-day/30-day gain.
    Prefer the first point on or after the cutoff. If the listing has no
    observation inside the window, fall back to the latest pre-window point.
    """
    after = [point for point in series if point.captured_at >= cutoff]
    if after:
        return after[0]
    before = [point for point in series if point.captured_at < cutoff]
    return before[-1] if before else None


def _nonnegative_gain(
    latest_value: int | None,
    baseline_value: int | None,
) -> int:
    if latest_value is None or baseline_value is None or latest_value < baseline_value:
        return 0
    return int(latest_value - baseline_value)


def _price_changes(series: list[SnapshotPoint], cutoff: datetime) -> int:
    previous_price = None
    changes = 0
    for point in series:
        if point.price_cents is None:
            continue
        if previous_price is not None and point.captured_at >= cutoff:
            if point.price_cents != previous_price:
                changes += 1
        previous_price = point.price_cents
    return changes


def _segment(
    *,
    age_days: int,
    favourites: int,
    favourites_gain_7d: int,
    strategy: dict[str, int],
) -> str:
    if favourites_gain_7d >= strategy["momentum_favourites_7d"]:
        return "momentum"
    if age_days >= strategy["very_stale_days"] and favourites <= strategy["low_favourites"]:
        return "low_interest_stale"
    if age_days >= strategy["stale_days"] and favourites >= strategy["high_favourites"]:
        return "high_interest_stale"
    return "steady"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(median(values)), 1)


def _sale_economics(
    sale: models.Sale,
    item: models.InventoryItem | None,
) -> dict[str, Any]:
    revenue = int(sale.total_cents) if sale.total_cents is not None else None
    cost = int(item.cost_cents) if item is not None and item.cost_cents is not None else None
    complete = revenue is not None and cost is not None
    profit = revenue - cost if complete else None
    margin_pct = (
        round(profit * 100 / revenue, 1)
        if complete and revenue > 0
        else None
    )
    roi_pct = (
        round(profit * 100 / cost, 1)
        if complete and cost > 0
        else None
    )
    attributes = dict(item.attributes or {}) if item is not None else {}
    return {
        "revenue_cents": revenue,
        "cost_cents": cost,
        "gross_profit_cents": profit,
        "gross_margin_pct": margin_pct,
        "roi_pct": roi_pct,
        "economics_complete": complete,
        "cost_source": attributes.get("cost_source"),
        "cost_source_adjusted": bool(attributes.get("cost_source_adjusted")),
    }


def build_vinted_analytics(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    days: int,
    strategy: dict[str, int],
    now: datetime | None = None,
    listing_limit: int | None = 200,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    days = max(7, min(int(days or 90), 365))
    window_start = now - timedelta(days=days)
    analysis_start = now - timedelta(days=max(days, 30))
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    listings = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace_id,
            models.ChannelListing.channel == Channel.VINTED,
        )
    ).scalars().all()
    listing_ids = [row.id for row in listings]
    series_by_listing = daily_snapshot_series(session, listing_ids, analysis_start)

    item_ids = {row.inventory_item_id for row in listings if row.inventory_item_id}
    items = (
        {
            row.id: row
            for row in session.execute(
                select(models.InventoryItem).where(
                    models.InventoryItem.workspace_id == workspace_id,
                    models.InventoryItem.id.in_(item_ids),
                )
            ).scalars().all()
        }
        if item_ids
        else {}
    )

    favourite_events = (
        session.execute(
            select(models.FavoriteEvent).where(
                models.FavoriteEvent.workspace_id == workspace_id,
                models.FavoriteEvent.channel_listing_id.in_(listing_ids),
                models.FavoriteEvent.occurred_at >= window_start,
            )
        ).scalars().all()
        if listing_ids
        else []
    )
    events_by_listing: dict[uuid.UUID, int] = {}
    for event in favourite_events:
        if event.channel_listing_id:
            events_by_listing[event.channel_listing_id] = (
                events_by_listing.get(event.channel_listing_id, 0) + 1
            )

    rows: list[dict[str, Any]] = []
    actual_age_count = 0
    segments = {
        "momentum": 0,
        "high_interest_stale": 0,
        "low_interest_stale": 0,
        "steady": 0,
    }
    age_buckets = {
        "0-29 days": 0,
        "30-59 days": 0,
        "60-89 days": 0,
        "90+ days": 0,
    }
    category_rows: dict[str, dict[str, Any]] = {}

    for listing in listings:
        if listing.status != ListingStatus.ACTIVE:
            continue
        started = listing_started_at(listing)
        started_source = listing_started_source(listing)
        if started_source == "vinted":
            actual_age_count += 1
        age_days = max(0, int((now - started).total_seconds() // 86400))
        if age_days < 30:
            age_buckets["0-29 days"] += 1
        elif age_days < 60:
            age_buckets["30-59 days"] += 1
        elif age_days < 90:
            age_buckets["60-89 days"] += 1
        else:
            age_buckets["90+ days"] += 1

        series = series_by_listing.get(listing.id, [])
        latest = series[-1] if series else None
        week = _window_baseline(series, week_start)
        window = _window_baseline(series, window_start)
        month = _window_baseline(series, month_start)
        views = int(latest.views or 0) if latest else 0
        favourites = int(latest.favourites or 0) if latest else 0
        views_gain_7d = _nonnegative_gain(
            latest.views if latest else None,
            week.views if week else None,
        )
        favourites_gain_7d = _nonnegative_gain(
            latest.favourites if latest else None,
            week.favourites if week else None,
        )
        views_gain_window = _nonnegative_gain(
            latest.views if latest else None,
            window.views if window else None,
        )
        favourites_gain_window = _nonnegative_gain(
            latest.favourites if latest else None,
            window.favourites if window else None,
        )
        price_changes_30d = _price_changes(series, month_start)
        price_delta_30d = (
            int(latest.price_cents) - int(month.price_cents)
            if latest
            and month
            and latest.price_cents is not None
            and month.price_cents is not None
            else None
        )
        favourite_rate = (
            round(favourites * 100 / views, 1)
            if views > 0
            else None
        )
        views_per_day = round(views / max(1, age_days), 1)
        item = items.get(listing.inventory_item_id)
        category = item.category if item else "general"
        segment = _segment(
            age_days=age_days,
            favourites=favourites,
            favourites_gain_7d=favourites_gain_7d,
            strategy=strategy,
        )
        segments[segment] += 1

        category_row = category_rows.setdefault(
            category,
            {
                "category": category,
                "active_listings": 0,
                "views": 0,
                "favourites": 0,
                "views_gain": 0,
                "favourites_gain": 0,
                "linked_sales": 0,
                "costed_sales": 0,
                "costed_revenue_cents": 0,
                "cost_cents": 0,
                "gross_profit_cents": 0,
            },
        )
        category_row["active_listings"] += 1
        category_row["views"] += views
        category_row["favourites"] += favourites
        category_row["views_gain"] += views_gain_window
        category_row["favourites_gain"] += favourites_gain_window

        rows.append(
            {
                "listing_id": str(listing.id),
                "item_id": str(listing.inventory_item_id) if listing.inventory_item_id else None,
                "title": listing.title,
                "url": listing.url,
                "category": category,
                "age_days": age_days,
                "listed_at": started.isoformat(),
                "listed_at_source": started_source,
                "price_cents": latest.price_cents if latest else listing.price_cents,
                "currency": listing.currency or "EUR",
                "views": views,
                "favourites": favourites,
                "views_gain_7d": views_gain_7d,
                "favourites_gain_7d": favourites_gain_7d,
                "views_gain_window": views_gain_window,
                "favourites_gain_window": favourites_gain_window,
                "favourites_per_100_views": favourite_rate,
                "views_per_day": views_per_day,
                "favourite_events_window": events_by_listing.get(listing.id, 0),
                "price_changes_30d": price_changes_30d,
                "price_delta_30d": price_delta_30d,
                "segment": segment,
                "tracked_since": series[0].captured_at.isoformat() if series else None,
            }
        )

    vinted_sales = session.execute(
        select(models.Sale).where(
            models.Sale.workspace_id == workspace_id,
            models.Sale.channel == Channel.VINTED,
            models.Sale.direction == "sell",
        )
    ).scalars().all()
    valid_sales = [row for row in vinted_sales if sale_counts_as_sold(row)]
    listings_by_item: dict[uuid.UUID, list[models.ChannelListing]] = {}
    for listing in listings:
        if listing.inventory_item_id:
            listings_by_item.setdefault(listing.inventory_item_id, []).append(listing)

    time_to_sale: list[float] = []
    sold_stock: list[dict[str, Any]] = []
    linked_sales = 0
    for sale in valid_sales:
        if not sale.inventory_item_id:
            continue
        sold_at, sold_at_source = sale_time(sale)
        if sold_at is None or sold_at < window_start:
            continue
        candidates = []
        for listing in listings_by_item.get(sale.inventory_item_id, []):
            started = listing_started_at(listing)
            if started <= sold_at:
                candidates.append((started, listing))
        if not candidates:
            continue
        started, _listing = max(candidates, key=lambda pair: pair[0])
        days_to_sale = max(
            0.0,
            (sold_at - started).total_seconds() / 86400,
        )
        time_to_sale.append(days_to_sale)
        linked_sales += 1
        item = items.get(sale.inventory_item_id)
        category = item.category if item else "general"
        economics = _sale_economics(sale, item)
        sold_stock.append(
            {
                "sale_id": str(sale.id),
                "listing_id": str(_listing.id),
                "item_id": str(sale.inventory_item_id),
                "title": sale.title or _listing.title,
                "category": category,
                "url": _listing.url,
                "listed_at": started.isoformat(),
                "listed_at_source": listing_started_source(_listing),
                "sold_at": sold_at.isoformat(),
                "sold_at_source": sold_at_source,
                "days_online": round(days_to_sale, 1),
                "sale_total_cents": sale.total_cents,
                "listing_price_cents": _listing.price_cents,
                "currency": sale.currency or _listing.currency or "EUR",
                "external_order_id": sale.external_order_id,
                **economics,
            }
        )
        category_row = category_rows.setdefault(
            category,
            {
                "category": category,
                "active_listings": 0,
                "views": 0,
                "favourites": 0,
                "views_gain": 0,
                "favourites_gain": 0,
                "linked_sales": 0,
                "costed_sales": 0,
                "costed_revenue_cents": 0,
                "cost_cents": 0,
                "gross_profit_cents": 0,
            },
        )
        category_row["linked_sales"] += 1
        if economics["economics_complete"]:
            category_row["costed_sales"] += 1
            category_row["costed_revenue_cents"] += int(economics["revenue_cents"])
            category_row["cost_cents"] += int(economics["cost_cents"])
            category_row["gross_profit_cents"] += int(economics["gross_profit_cents"])

    rows.sort(
        key=lambda row: (
            int(row["favourites_gain_7d"]),
            int(row["views_gain_7d"]),
            int(row["favourites"]),
            int(row["views"]),
        ),
        reverse=True,
    )

    active_ages = [int(row["age_days"]) for row in rows]
    current_views = sum(int(row["views"]) for row in rows)
    current_favourites = sum(int(row["favourites"]) for row in rows)
    window_views = sum(int(row["views_gain_window"]) for row in rows)
    window_favourites = sum(int(row["favourites_gain_window"]) for row in rows)
    price_changes = sum(int(row["price_changes_30d"]) for row in rows)

    complete_sold_rows = [row for row in sold_stock if row["economics_complete"]]
    costed_revenue_cents = sum(int(row["revenue_cents"]) for row in complete_sold_rows)
    acquisition_cost_cents = sum(int(row["cost_cents"]) for row in complete_sold_rows)
    gross_profit_cents = sum(int(row["gross_profit_cents"]) for row in complete_sold_rows)
    gross_margin_pct = (
        round(gross_profit_cents * 100 / costed_revenue_cents, 1)
        if costed_revenue_cents > 0
        else None
    )
    roi_pct = (
        round(gross_profit_cents * 100 / acquisition_cost_cents, 1)
        if acquisition_cost_cents > 0
        else None
    )

    category_result = sorted(
        category_rows.values(),
        key=lambda row: (
            int(row["favourites_gain"]),
            int(row["views_gain"]),
            int(row["active_listings"]),
        ),
        reverse=True,
    )
    for category_row in category_result:
        views = int(category_row["views"])
        favourites = int(category_row["favourites"])
        category_row["favourites_per_100_views"] = (
            round(favourites * 100 / views, 1) if views > 0 else None
        )
        category_revenue = int(category_row["costed_revenue_cents"])
        category_cost = int(category_row["cost_cents"])
        category_profit = int(category_row["gross_profit_cents"])
        category_row["cost_coverage_pct"] = (
            round(int(category_row["costed_sales"]) * 100 / int(category_row["linked_sales"]), 1)
            if int(category_row["linked_sales"]) > 0
            else None
        )
        category_row["gross_margin_pct"] = (
            round(category_profit * 100 / category_revenue, 1)
            if category_revenue > 0
            else None
        )
        category_row["roi_pct"] = (
            round(category_profit * 100 / category_cost, 1)
            if category_cost > 0
            else None
        )

    analytics_currency = next(
        (row["currency"] for row in sold_stock if row.get("currency")),
        next((row["currency"] for row in rows if row.get("currency")), "EUR"),
    )

    return {
        "days": days,
        "currency": analytics_currency,
        "strategy": strategy,
        "summary": {
            "active_listings": len(rows),
            "tracked_active_listings": sum(
                1 for row in rows if row["tracked_since"] is not None
            ),
            "views": current_views,
            "favourites": current_favourites,
            "views_gained": window_views,
            "favourites_gained": window_favourites,
            "favourite_events": len(favourite_events),
            "favourites_per_100_views": (
                round(current_favourites * 100 / current_views, 1)
                if current_views > 0
                else None
            ),
            "median_active_age_days": _median([float(value) for value in active_ages]),
            "actual_age_count": actual_age_count,
            "linked_sales": linked_sales,
            "costed_linked_sales": len(complete_sold_rows),
            "cost_coverage_pct": (
                round(len(complete_sold_rows) * 100 / linked_sales, 1)
                if linked_sales
                else None
            ),
            "costed_revenue_cents": costed_revenue_cents,
            "acquisition_cost_cents": acquisition_cost_cents,
            "gross_profit_cents": gross_profit_cents,
            "gross_margin_pct": gross_margin_pct,
            "roi_pct": roi_pct,
            "median_days_to_sale": _median(time_to_sale),
            "price_changes_30d": price_changes,
        },
        "age_buckets": [
            {"label": label, "count": count}
            for label, count in age_buckets.items()
        ],
        "segments": [
            {"segment": segment, "count": count}
            for segment, count in segments.items()
        ],
        "categories": category_result,
        "sold_stock": sorted(
            sold_stock,
            key=lambda row: row["sold_at"],
            reverse=True,
        )[:500],
        "listings": rows if listing_limit is None else rows[:max(0, int(listing_limit))],
    }
