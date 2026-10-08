from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event

# Event types (Functional Requirements §14)
USER_REGISTERED = "USER_REGISTERED"
BOT_STARTED = "BOT_STARTED"
SEARCH_STARTED = "SEARCH_STARTED"
SEARCH_COMPLETED = "SEARCH_COMPLETED"
AUCTION_OPENED = "AUCTION_OPENED"
MONITOR_CREATED = "MONITOR_CREATED"
MONITOR_UPDATED = "MONITOR_UPDATED"
MONITOR_DELETED = "MONITOR_DELETED"
AUCTION_NOTIFICATION_SENT = "AUCTION_NOTIFICATION_SENT"
CONSULTATION_REQUESTED = "CONSULTATION_REQUESTED"
CONSULTATION_TAKEN = "CONSULTATION_TAKEN"
CONSULTATION_COMPLETED = "CONSULTATION_COMPLETED"
CAMPAIGN_SENT = "CAMPAIGN_SENT"
# Additional service events
MARKETING_PREFERENCE_CHANGED = "MARKETING_PREFERENCE_CHANGED"
PRIVACY_REQUEST_CREATED = "PRIVACY_REQUEST_CREATED"
ROLE_GRANTED = "ROLE_GRANTED"
ROLE_REVOKED = "ROLE_REVOKED"


async def log_event(
    session: AsyncSession,
    event_type: str,
    user_id: int | None = None,
    actor_user_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        Event(
            event_type=event_type,
            user_id=user_id,
            actor_user_id=actor_user_id,
            event_data=data or {},
        )
    )
