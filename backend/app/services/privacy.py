from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PrivacyRequest, User
from app.services import events

OPEN_STATUSES = ("NEW", "IN_PROGRESS")


async def create_deletion_request(session: AsyncSession, user: User) -> tuple[PrivacyRequest, bool]:
    """Create a DATA_DELETION request. Returns (request, created).

    Only one open request per user (partial unique index), so repeated taps
    return the already existing request.
    """
    new_id = await session.scalar(
        insert(PrivacyRequest)
        .values(user_id=user.id, type="DATA_DELETION", status="NEW")
        .on_conflict_do_nothing(
            index_elements=[PrivacyRequest.user_id],
            index_where=PrivacyRequest.status.in_(OPEN_STATUSES),
        )
        .returning(PrivacyRequest.id)
    )
    if new_id is None:
        existing = await session.scalar(
            select(PrivacyRequest).where(
                PrivacyRequest.user_id == user.id, PrivacyRequest.status.in_(OPEN_STATUSES)
            )
        )
        assert existing is not None
        return existing, False
    await events.log_event(
        session, events.PRIVACY_REQUEST_CREATED, user_id=user.id, data={"request_id": new_id}
    )
    req = await session.get(PrivacyRequest, new_id)
    assert req is not None
    return req, True
