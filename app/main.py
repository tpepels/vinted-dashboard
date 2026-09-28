from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.email_parser import money_to_cents, normalize_title
from app.imap_sync import sync_imap
from app.models import Listing, Notification, Order


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Vinted Dashboard", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def listing_json(row: Listing) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "price_cents": row.price_cents,
        "currency": row.currency,
        "status": row.status,
        "isbn": row.isbn,
        "vinted_url": row.vinted_url,
        "image_url": row.image_url,
        "listed_at": dt(row.listed_at),
        "sold_at": dt(row.sold_at),
        "created_at": dt(row.created_at),
        "updated_at": dt(row.updated_at),
    }


def order_json(row: Order) -> dict[str, Any]:
    return {
        "id": row.id,
        "direction": row.direction,
        "title": row.title,
        "counterparty": row.counterparty,
        "total_cents": row.total_cents,
        "item_cents": row.item_cents,
        "shipping_cents": row.shipping_cents,
        "protection_cents": row.protection_cents,
        "currency": row.currency,
        "status": row.status,
        "tracking_code": row.tracking_code,
        "ship_by": dt(row.ship_by),
        "estimated_delivery": row.estimated_delivery,
        "transaction_id": row.transaction_id,
        "created_at": dt(row.created_at),
        "updated_at": dt(row.updated_at),
        "closed_at": dt(row.closed_at),
    }


def notification_json(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "order_id": row.order_id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "occurred_at": dt(row.occurred_at),
        "read_at": dt(row.read_at),
    }


class ListingInput(BaseModel):
    title: str
    price: float | None = None
    status: str = "active"
    isbn: str | None = None
    vinted_url: str | None = None
    image_url: str | None = None
    listed_at: datetime | None = None


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/settings")
def settings():
    return {
        "profile_url": os.getenv("VINTED_PROFILE_URL", "https://www.vinted.pt/member/58344842"),
        "username": os.getenv("VINTED_USERNAME", "tom_waits"),
        "email_sync_configured": bool(
            os.getenv("EMAIL_USERNAME", "").strip() and os.getenv("EMAIL_APP_PASSWORD", "").strip()
        ),
    }


@app.get("/api/summary")
def summary(db: Session = Depends(db_session)):
    active = db.scalar(select(func.count()).select_from(Listing).where(Listing.status == "active")) or 0
    open_sales = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.direction == "sell", Order.status.notin_(["completed", "cancelled"])
        )
    ) or 0
    open_buys = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.direction == "buy", Order.status.notin_(["completed", "cancelled"])
        )
    ) or 0
    unread = db.scalar(
        select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    ) or 0
    completed_sales = db.scalar(
        select(func.count()).select_from(Order).where(Order.direction == "sell", Order.status == "completed")
    ) or 0
    revenue = db.scalar(
        select(func.coalesce(func.sum(Order.total_cents), 0)).where(
            Order.direction == "sell", Order.status == "completed"
        )
    ) or 0

    attention = db.scalars(
        select(Order)
        .where(
            Order.status.in_(["awaiting_shipment", "label_ready", "ready_for_pickup", "confirmation_needed"])
        )
        .order_by(Order.updated_at.desc())
        .limit(8)
    ).all()

    return {
        "active_listings": active,
        "open_sales": open_sales,
        "open_purchases": open_buys,
        "unread_notifications": unread,
        "completed_sales": completed_sales,
        "revenue_cents": revenue,
        "attention": [order_json(x) for x in attention],
    }


@app.get("/api/listings")
def listings(status: str | None = None, db: Session = Depends(db_session)):
    query = select(Listing)
    if status:
        query = query.where(Listing.status == status)
    rows = db.scalars(query.order_by(Listing.updated_at.desc(), Listing.id.desc())).all()
    return [listing_json(x) for x in rows]


@app.post("/api/listings")
def add_listing(payload: ListingInput, db: Session = Depends(db_session)):
    row = Listing(
        title=payload.title.strip(),
        normalized_title=normalize_title(payload.title),
        price_cents=round(payload.price * 100) if payload.price is not None else None,
        status=payload.status,
        isbn=payload.isbn,
        vinted_url=payload.vinted_url,
        image_url=payload.image_url,
        listed_at=payload.listed_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return listing_json(row)


@app.patch("/api/listings/{listing_id}/status")
def update_listing_status(listing_id: int, status: str, db: Session = Depends(db_session)):
    row = db.get(Listing, listing_id)
    if not row:
        raise HTTPException(status_code=404, detail="Listing not found")
    row.status = status
    if status == "sold" and row.sold_at is None:
        row.sold_at = datetime.now(timezone.utc)
    db.commit()
    return listing_json(row)


@app.post("/api/listings/import-csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(db_session)):
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header")

    aliases = {
        "title": ["title", "name", "item", "item_name"],
        "price": ["price", "amount", "item_price"],
        "status": ["status", "state"],
        "isbn": ["isbn", "isbn13"],
        "vinted_url": ["vinted_url", "url", "link"],
        "image_url": ["image_url", "image", "photo"],
        "listed_at": ["listed_at", "created_at", "date"],
    }

    def value(row: dict[str, str], key: str) -> str | None:
        lowered = {str(k).strip().lower(): v for k, v in row.items()}
        for alias in aliases[key]:
            found = lowered.get(alias)
            if found is not None and str(found).strip():
                return str(found).strip()
        return None

    imported = 0
    updated = 0
    for raw_row in reader:
        title = value(raw_row, "title")
        if not title:
            continue
        normalized = normalize_title(title)
        existing = db.scalar(
            select(Listing).where(Listing.normalized_title == normalized).order_by(Listing.id.desc())
        )
        price_cents = money_to_cents(value(raw_row, "price"))
        listed_at = None
        raw_date = value(raw_row, "listed_at")
        if raw_date:
            try:
                listed_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                listed_at = None

        target = existing or Listing(title=title, normalized_title=normalized)
        target.title = title
        target.price_cents = price_cents if price_cents is not None else target.price_cents
        target.status = value(raw_row, "status") or target.status or "active"
        target.isbn = value(raw_row, "isbn") or target.isbn
        target.vinted_url = value(raw_row, "vinted_url") or target.vinted_url
        target.image_url = value(raw_row, "image_url") or target.image_url
        target.listed_at = listed_at or target.listed_at

        if existing:
            updated += 1
        else:
            db.add(target)
            imported += 1

    db.commit()
    return {"imported": imported, "updated": updated}


@app.get("/api/orders")
def orders(direction: str | None = None, status: str | None = None, db: Session = Depends(db_session)):
    query = select(Order)
    if direction:
        query = query.where(Order.direction == direction)
    if status:
        query = query.where(Order.status == status)
    rows = db.scalars(query.order_by(Order.updated_at.desc(), Order.id.desc())).all()
    return [order_json(x) for x in rows]


@app.get("/api/notifications")
def notifications(unread_only: bool = False, db: Session = Depends(db_session)):
    query = select(Notification)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = db.scalars(query.order_by(Notification.occurred_at.desc()).limit(500)).all()
    return [notification_json(x) for x in rows]


@app.post("/api/notifications/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(db_session)):
    row = db.get(Notification, notification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.read_at = datetime.now(timezone.utc)
    db.commit()
    return notification_json(row)


@app.post("/api/notifications/read-all")
def mark_all_read(db: Session = Depends(db_session)):
    rows = db.scalars(select(Notification).where(Notification.read_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.read_at = now
    db.commit()
    return {"updated": len(rows)}


@app.post("/api/sync/email")
def sync_email(db: Session = Depends(db_session)):
    try:
        return sync_imap(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Email sync failed: {exc}") from exc
