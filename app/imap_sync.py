from __future__ import annotations

import email
import imaplib
import os
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from app.email_parser import parse_vinted_email
from app.service import apply_event


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _message_body(msg: Message) -> str:
    plain: list[str] = []
    html: list[str] = []

    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in disposition.lower():
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if content_type == "text/plain":
            plain.append(text)
        else:
            html.append(text)

    if plain:
        return "\n".join(plain)

    extractor = _TextExtractor()
    extractor.feed("\n".join(html))
    return extractor.text()


def _parse_date(msg: Message) -> datetime:
    raw = msg.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _looks_like_vinted(sender: str, body: str) -> bool:
    sender_lower = sender.lower()
    body_lower = body.lower()
    return (
        "vinted" in sender_lower
        or "this email was sent by vinted" in body_lower
        or "vinted forwarded this email" in body_lower
        or "we are required to send you this email" in body_lower and "vinted" in body_lower
    )


def sync_imap(session: Session) -> dict:
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    port = int(os.getenv("IMAP_PORT", "993"))
    username = os.getenv("EMAIL_USERNAME", "").strip()
    password = os.getenv("EMAIL_APP_PASSWORD", "").strip()
    mailbox = os.getenv("IMAP_MAILBOX", "[Gmail]/All Mail")
    days = int(os.getenv("EMAIL_SYNC_DAYS", "180"))
    max_messages = int(os.getenv("EMAIL_SYNC_MAX_MESSAGES", "1000"))

    if not username or not password:
        raise RuntimeError("EMAIL_USERNAME and EMAIL_APP_PASSWORD are not configured")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")

    client = imaplib.IMAP4_SSL(host, port)
    try:
        client.login(username, password)
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not select IMAP mailbox: {mailbox}")

        status, data = client.search(None, "SINCE", since)
        if status != "OK":
            raise RuntimeError("IMAP search failed")

        ids = data[0].split()[-max_messages:]
        messages: list[tuple[datetime, str, str, str, str]] = []

        for imap_id in ids:
            status, chunks = client.fetch(imap_id, "(RFC822)")
            if status != "OK" or not chunks:
                continue
            raw = next((c[1] for c in chunks if isinstance(c, tuple) and len(c) > 1), None)
            if not raw:
                continue

            msg = email.message_from_bytes(raw)
            subject = _decode(msg.get("Subject"))
            sender = _decode(msg.get("From"))
            body = _message_body(msg)

            if not _looks_like_vinted(sender, body):
                continue

            message_id = (msg.get("Message-ID") or f"imap:{imap_id.decode()}").strip("<>")
            messages.append((_parse_date(msg), message_id, sender, subject, body))

        messages.sort(key=lambda x: x[0])

        imported = 0
        for occurred_at, message_id, sender, subject, body in messages:
            event = parse_vinted_email(subject, body)
            notification = apply_event(
                session=session,
                event=event,
                source_message_id=message_id,
                sender=sender,
                subject=subject,
                occurred_at=occurred_at,
            )
            if notification is not None:
                imported += 1

        session.commit()
        return {
            "checked": len(ids),
            "vinted_messages": len(messages),
            "imported": imported,
        }
    finally:
        try:
            client.logout()
        except Exception:
            pass
