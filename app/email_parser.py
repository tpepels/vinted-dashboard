from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ParsedEvent:
    kind: str
    title: str
    body: str
    status: str | None = None
    direction: str | None = None
    counterparty: str | None = None
    total_cents: int | None = None
    item_cents: int | None = None
    shipping_cents: int | None = None
    protection_cents: int | None = None
    currency: str = "EUR"
    tracking_code: str | None = None
    ship_by: datetime | None = None
    estimated_delivery: str | None = None
    transaction_id: str | None = None


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def money_to_cents(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip().replace("€", "").replace(" ", "")
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    elif "," in value and "." in value:
        value = value.replace(",", "")
    try:
        return round(float(value) * 100)
    except ValueError:
        return None


def _field(body: str, label: str) -> Optional[str]:
    match = re.search(rf"(?im)^\s*{re.escape(label)}\s*$\s*^\s*(.+?)\s*$", body)
    return match.group(1).strip() if match else None


def _parse_vinted_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H.%M"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    return None


def parse_vinted_email(subject: str, body: str) -> ParsedEvent:
    subject = (subject or "").strip()
    body = (body or "").replace("\r\n", "\n")
    lower_subject = subject.lower()
    lower_body = body.lower()

    if "sold an item on vinted" in lower_subject:
        match = re.search(
            r"(?is)hello\s+[^,]+,\s*(?P<buyer>[^\n]+?)\s+has bought\s+(?P<title>.+?)\s+€(?P<price>[\d.,]+)",
            body,
        )
        if match:
            return ParsedEvent(
                kind="sale_created",
                direction="sell",
                status="awaiting_shipment",
                title=match.group("title").strip(),
                counterparty=match.group("buyer").strip(),
                total_cents=money_to_cents(match.group("price")),
                body="Buyer paid. Shipment still needs to be sent.",
            )

    label_match = re.match(
        r"(?is)^(?P<title>.+?)\s+shipping label\s+[–-]\s+use by\s+(?P<due>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})$",
        subject,
    )
    if label_match:
        tracking = _field(body, "Tracking code") or _field(body, "Tracking Code")
        return ParsedEvent(
            kind="shipping_label",
            direction="sell",
            status="label_ready",
            title=label_match.group("title").strip(),
            tracking_code=tracking,
            ship_by=_parse_vinted_date(label_match.group("due")),
            body="Shipping label generated.",
        )

    if lower_subject.startswith("we’ll take it from here!") or lower_subject.startswith("we'll take it from here!"):
        tracking = _field(body, "Tracking code")
        order_title = _field(body, "Order details") or subject
        return ParsedEvent(
            kind="carrier_received",
            direction="sell",
            status="shipped",
            title=order_title,
            tracking_code=tracking,
            body="Carrier has received the parcel.",
        )

    if lower_subject.startswith("your receipt for"):
        quote = re.search(r'["“](.+?)[”"]', subject)
        title = quote.group(1).strip() if quote else subject.removeprefix("Your receipt for").strip()
        return ParsedEvent(
            kind="purchase_created",
            direction="buy",
            status="paid",
            title=title,
            counterparty=_field(body, "Seller"),
            total_cents=money_to_cents(_field(body, "Paid")),
            item_cents=money_to_cents(_field(body, "Item")),
            shipping_cents=money_to_cents(_field(body, "Postage")),
            protection_cents=money_to_cents(_field(body, "Buyer Protection fee")),
            transaction_id=(_field(body, "Transaction ID") or "").lstrip("#") or None,
            body="Purchase payment received by Vinted.",
        )

    if lower_subject == "this order is completed":
        match = re.search(
            r"(?is)your sale of\s+(?P<title>.+?)\s+was completed successfully",
            body,
        )
        title = match.group("title").strip() if match else subject
        transaction = re.search(r"(?im)^\s*Transaction ID:\s*#?([^\s]+)", body)
        return ParsedEvent(
            kind="sale_completed",
            direction="sell",
            status="completed",
            title=title,
            transaction_id=transaction.group(1).strip() if transaction else None,
            body="Sale completed and payment released.",
        )

    if lower_subject.endswith(" - confirmation needed"):
        title = subject[: -len(" - Confirmation needed")].strip()
        return ParsedEvent(
            kind="purchase_confirmation_needed",
            direction="buy",
            status="confirmation_needed",
            title=title,
            body="Purchase was marked delivered and needs confirmation.",
        )

    if lower_subject.startswith("order update for "):
        title = subject[len("Order update for "):].strip()
        if "on its way to the buyer" in lower_body:
            est = None
            m = re.search(r"Estimated delivery is\s+(.+?)(?:\s+for\s+|\.|\n)", body, re.I)
            if m:
                est = m.group(1).strip()
            return ParsedEvent(
                kind="sale_shipped",
                direction="sell",
                status="shipped",
                title=title,
                estimated_delivery=est,
                body="Parcel is on its way to the buyer.",
            )
        if "has received their order" in lower_body:
            return ParsedEvent(
                kind="buyer_received",
                direction="sell",
                status="buyer_received",
                title=title,
                body="Buyer received the parcel. Vinted is waiting out the issue-reporting window before releasing payment.",
            )
        if re.search(r"we(?:'re| are) waiting for .+ to collect their order", lower_body):
            return ParsedEvent(
                kind="buyer_pickup",
                direction="sell",
                status="ready_for_pickup",
                title=title,
                body="Parcel is ready for the buyer to collect.",
            )
        if "your purchase is waiting for you" in lower_body or ("waiting for you" in lower_body and "collect" in lower_body):
            return ParsedEvent(
                kind="purchase_pickup",
                direction="buy",
                status="ready_for_pickup",
                title=title,
                body="Purchase is ready for collection.",
            )
        if "your parcel is on its way" in lower_body:
            return ParsedEvent(
                kind="purchase_shipped",
                direction="buy",
                status="shipped",
                title=title,
                body="Purchase is on its way.",
            )
        return ParsedEvent(kind="order_update", title=title, body=body[:500].strip())

    if lower_subject == "leave feedback for the seller":
        item = re.search(r"(?is)congrats on your lovely purchase\s+(.+?)\.\s+how did everything go", body)
        title = item.group(1).strip() if item else "Completed purchase"
        return ParsedEvent(
            kind="purchase_completed",
            direction="buy",
            status="completed",
            title=title,
            body="Purchase completed. Vinted is asking for seller feedback.",
        )

    if any(x in lower_subject for x in ("order cancelled", "order canceled", "transaction cancelled", "transaction canceled")):
        return ParsedEvent(
            kind="order_cancelled",
            status="cancelled",
            title=subject,
            body=body[:500].strip(),
        )

    if any(x in lower_subject for x in ("order complete", "order completed", "transaction complete", "transaction completed")):
        direction = "sell" if "vinted balance" in lower_body or "payment" in lower_body else None
        return ParsedEvent(
            kind="order_completed",
            direction=direction,
            status="completed",
            title=subject,
            body=body[:500].strip(),
        )

    return ParsedEvent(
        kind="vinted_email",
        title=subject or "Vinted notification",
        body=re.sub(r"\s+", " ", body).strip()[:500],
    )
