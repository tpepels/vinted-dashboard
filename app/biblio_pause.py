"""Durable, workspace-scoped BIBLIO upload pause.

The worker remains active for other marketplaces. A pause automatically expires,
and does not change the user's independent Vinted-triggered auto-sync switch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import db, models
from app.constants import Channel
from app.workspace_bootstrap import get_or_create_channel_account

PAUSE_CONFIG_KEY = "upload_paused_until"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def biblio_paused_until(
    workspace_id: uuid.UUID, *, now: datetime | None = None,
) -> datetime | None:
    with db.session_scope() as session:
        account = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == workspace_id,
                models.ChannelAccount.channel == Channel.BIBLIO,
            )
        ).scalar_one_or_none()
        raw = (account.config or {}).get(PAUSE_CONFIG_KEY) if account else None
    if not raw:
        return None
    try:
        expiry = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if expiry.tzinfo is None:
        return None
    expiry = expiry.astimezone(timezone.utc)
    return expiry if expiry > (now or utcnow()) else None


def set_biblio_pause(workspace: models.Workspace, minutes: int) -> datetime | None:
    """Set a fixed-duration pause, or resume immediately with zero minutes."""
    if minutes not in (0, 60):
        raise ValueError("Pause length must be zero or sixty minutes")
    until = utcnow() + timedelta(minutes=minutes) if minutes else None
    with db.session_scope() as session:
        account, _ = get_or_create_channel_account(
            session, workspace, Channel.BIBLIO, {},
        )
        account.config = {
            **dict(account.config or {}),
            PAUSE_CONFIG_KEY: until.isoformat() if until else None,
        }
    return until
