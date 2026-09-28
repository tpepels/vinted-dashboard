from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.email_parser import ParsedEvent, normalize_title
from app.models import Listing, Notification, Order, ProcessedMessage


STATUS_RANK = {
    "open": 0,
    "paid": 10,
    "awaiting_shipment": 10,
    "label_ready": 20,
    "shipped": 30,
    "ready_for_pickup": 40,
    "completed": 100,
    "cancelled": 100,
}


def _short_title(event: ParsedEvent) -> str:
    labels = {
        "sale_created": "Sold",
        "shipping_label": "Shipping label ready",
        "sale_shipped": "Sale shipped",
        "carrier_received": "Carrier received parcel",
        "buyer_pickup": "Buyer pickup pending",
        "purchase_created": "Purchase paid",
        "purchase_shipped": "Purchase shipped",
        "purchase_pickup": "Purchase ready for pickup",
        "purchase_completed": "Purchase completed",
        "order_cancelled": "Order cancelled",
        "order_completed": "Order completed",
    }
    prefix = labels.get(event.kind, "Vinted")
    return f"{prefix}: {event.title}"


def _find_order(session: Session, event: ParsedEvent) -> Order | None:
    if event.transaction_id:
        order = session.scalar(select(Order).where(Order.transaction_id == event.transaction_id))
        if order:
            return order

    normalized = normalize_title(event.title)
    query = select(Order).where(Order.normalized_title == normalized)
    if event.direction:
        query = query.where(Order.direction == event.direction)
    order = session.scalar(query.order_by(Order.id.desc()))
    if order:
        return order

    # Vinted sometimes changes bundle wording between emails. Use a conservative fallback.
    tokens = [t for t in re.split(r"\W+", normalized) if len(t) >= 5][:4]
    if not tokens:
        return None
    candidates = session.scalars(select(Order).order_by(Order.id.desc()).limit(100)).all()
    for candidate in candidates:
        if event.direction and candidate.direction != event.direction:
            continue
        score = sum(t in candidate.normalized_title for t in tokens)
        if score >= min(2, len(tokens)):
            return candidate
    return None


def _mark_listing_sold(session: Session, event: ParsedEvent, occurred_at: datetime) -> None:
    normalized = normalize_title(event.title)
    listing = session.scalar(
        select(Listing).where(Listing.normalized_title == normalized).order_by(Listing.id.desc())
    )
    if listing is None:
        listing = Listing(
            title=event.title,
            normalized_title=normalized,
            price_cents=event.total_cents,
            currency=event.currency,
            status="sold",
            sold_at=occurred_at,
        )
        session.add(listing)
        return

    listing.status = "sold"
    listing.sold_at = occurred_at
    if event.total_cents is not None:
        listing.price_cents = event.total_cents


def apply_event(
    session: Session,
    event: ParsedEvent,
    source_message_id: str,
    sender: str,
    subject: str,
    occurred_at: datetime,
) -> Notification:
    occurred_at = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=timezone.utc)

    processed = session.scalar(
        select(ProcessedMessage).where(ProcessedMessage.source_message_id == source_message_id)
    )
    if processed:
        notification = session.scalar(
            select(Notification).where(Notification.source_message_id == source_message_id)
        )
        return notification

    order = _find_order(session, event)

    if event.direction and order is None:
        order = Order(
            direction=event.direction,
            title=event.title,
            normalized_title=normalize_title(event.title),
            status=event.status or "open",
            created_at=occurred_at,
        )
        session.add(order)
        session.flush()

    if order is not None:
        if event.counterparty:
            order.counterparty = event.counterparty
        for attr in (
            "total_cents",
            "item_cents",
            "shipping_cents",
            "protection_cents",
            "tracking_code",
            "ship_by",
            "estimated_delivery",
            "transaction_id",
        ):
            value = getattr(event, attr)
            if value is not None:
                setattr(order, attr, value)

        if event.status:
            current_rank = STATUS_RANK.get(order.status, 0)
            new_rank = STATUS_RANK.get(event.status, 0)
            if new_rank >= current_rank or event.status in {"cancelled", "completed"}:
                order.status = event.status
                if event.status in {"cancelled", "completed"}:
                    order.closed_at = occurred_at
        order.updated_at = occurred_at

    if event.kind == "sale_created":
        _mark_listing_sold(session, event, occurred_at)

    notification = Notification(
        order_id=order.id if order else None,
        kind=event.kind,
        title=_short_title(event),
        body=event.body,
        source_message_id=source_message_id,
        occurred_at=occurred_at,
    )
    session.add(notification)
    session.add(
        ProcessedMessage(
            source_message_id=source_message_id,
            subject=subject,
            sender=sender,
            occurred_at=occurred_at,
        )
    )
    session.flush()
    return notification
