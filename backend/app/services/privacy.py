from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.thetender import TenderClient, TenderError
from app.models import (
    BotSession,
    ConsultationRequest,
    Monitoring,
    MonitoringEditDraft,
    NotificationDelivery,
    PrivacyRequest,
    SearchDraft,
    SearchSnapshot,
    User,
    UserRole,
)
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


class DeletionResult:
    """Outcome of one processing attempt (the admin may repeat it safely)."""

    def __init__(self, ok: bool, error: str | None = None, user: User | None = None) -> None:
        self.ok = ok
        self.error = error
        self.user = user


async def process_deletion(
    session: AsyncSession, tender: TenderClient, request_id: int, admin: User
) -> DeletionResult:
    """Remote-first deletion (FR §12): delete the user's monitorings on The
    Tender, and only after that anonymize local personal data and mark the
    request COMPLETED. A failure leaves IN_PROGRESS with last_error; repeating
    is safe (already deleted monitorings are skipped)."""
    req = await session.get(PrivacyRequest, request_id, with_for_update=True)
    if req is None:
        return DeletionResult(False, "not_found")
    user = await session.get(User, req.user_id)
    assert user is not None
    if req.status == "COMPLETED":
        return DeletionResult(True, user=user)
    if await session.scalar(select(UserRole.id).where(UserRole.user_id == user.id).limit(1)):
        return DeletionResult(False, "staff")

    req.status = "IN_PROGRESS"
    req.attempts += 1
    req.handled_by = admin.id

    # 1. Remote cleanup on The Tender (canonical monitoring storage).
    try:
        remote = await tender.list_monitorings(str(user.telegram_id))
        for ext in remote:
            try:
                await tender.delete_monitoring(ext.id)
            except TenderError as exc:
                if exc.kind != "not_found":
                    raise
    except TenderError as exc:
        req.last_error = f"The Tender API: {exc}"[:1000]
        return DeletionResult(False, req.last_error, user)

    # 2. Local anonymization (only after successful remote cleanup).
    now = datetime.now(UTC)
    tg_id = user.telegram_id
    user.name = None
    user.username = None
    user.phone = None
    user.anonymized_at = now
    await session.execute(
        update(ConsultationRequest)
        .where(ConsultationRequest.user_id == user.id)
        .values(contact_name=None, contact_phone=None, contact_username=None)
    )
    await session.execute(
        update(Monitoring)
        .where(Monitoring.user_id == user.id, Monitoring.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    await session.execute(delete(MonitoringEditDraft).where(MonitoringEditDraft.user_id == user.id))
    await session.execute(delete(SearchDraft).where(SearchDraft.user_id == user.id))
    await session.execute(delete(SearchSnapshot).where(SearchSnapshot.user_id == user.id))
    await session.execute(delete(BotSession).where(BotSession.telegram_user_id == tg_id))
    await session.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.telegram_user_id == tg_id,
            NotificationDelivery.status.in_(("PENDING", "SENDING")),
        )
        .values(status="SKIPPED", last_error="personal data deleted")
    )

    req.status = "COMPLETED"
    req.completed_at = now
    req.last_error = None
    await events.log_event(
        session,
        events.PRIVACY_REQUEST_COMPLETED,
        user_id=user.id,
        actor_user_id=admin.id,
        data={"request_id": req.id, "monitorings_deleted": len(remote)},
    )
    return DeletionResult(True, user=user)
