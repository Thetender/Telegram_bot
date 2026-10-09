"""Consultation request lifecycle (FR §9, §10, §17).

NEW → IN_PROGRESS (atomic claim by first Manager) → COMPLETED (INTERESTED/DECLINED).
An Admin may close NEW / IN_PROGRESS as CLOSED_BY_ADMIN (Dashboard, Iteration 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CONSULTATION_OPEN,
    ROLE_ADMIN,
    ROLE_MANAGER,
    AdminSettings,
    ConsultationNotification,
    ConsultationRequest,
    ManagerSettings,
    User,
    UserRole,
)
from app.services import events

RESULTS = ("INTERESTED", "DECLINED")


def is_weekend(now: datetime, tz: str) -> bool:
    """v1: Saturday/Sunday in the business time zone; no holiday calendar."""
    return now.astimezone(ZoneInfo(tz)).weekday() >= 5


async def get_open_request(session: AsyncSession, user_id: int) -> ConsultationRequest | None:
    return await session.scalar(
        select(ConsultationRequest).where(
            ConsultationRequest.user_id == user_id,
            ConsultationRequest.status.in_(CONSULTATION_OPEN),
        )
    )


async def create_request(session: AsyncSession, user: User) -> tuple[ConsultationRequest, bool]:
    """Create a request unless the user already has an open one.

    Enforced atomically by the partial unique index; returns (request, created).
    """
    new_id = await session.scalar(
        insert(ConsultationRequest)
        .values(
            user_id=user.id,
            contact_name=user.name,
            contact_phone=user.phone,
            contact_username=user.username,
            status="NEW",
        )
        .on_conflict_do_nothing(
            index_elements=[ConsultationRequest.user_id],
            index_where=ConsultationRequest.status.in_(CONSULTATION_OPEN),
        )
        .returning(ConsultationRequest.id)
    )
    if new_id is None:
        existing = await get_open_request(session, user.id)
        assert existing is not None
        return existing, False
    await events.log_event(
        session, events.CONSULTATION_REQUESTED, user_id=user.id, data={"request_id": new_id}
    )
    req = await session.get(ConsultationRequest, new_id)
    assert req is not None
    return req, True


async def notification_targets(session: AsyncSession) -> tuple[list[User], list[User]]:
    """(managers with consultation notifications ON, admins with system notifications ON)."""
    managers = (
        await session.scalars(
            select(User)
            .join(UserRole, (UserRole.user_id == User.id) & (UserRole.role == ROLE_MANAGER))
            .outerjoin(ManagerSettings, ManagerSettings.user_id == User.id)
            .where(
                User.anonymized_at.is_(None),
                ManagerSettings.consultation_notifications_enabled.is_not(False),
            )
            .order_by(User.id)
        )
    ).all()
    admins = (
        await session.scalars(
            select(User)
            .join(UserRole, (UserRole.user_id == User.id) & (UserRole.role == ROLE_ADMIN))
            .outerjoin(AdminSettings, AdminSettings.user_id == User.id)
            .where(
                User.anonymized_at.is_(None),
                AdminSettings.system_notifications_enabled.is_not(False),
            )
            .order_by(User.id)
        )
    ).all()
    return list(managers), list(admins)


async def record_notification(
    session: AsyncSession,
    request_id: int,
    recipient: User,
    kind: str,
    message_id: int | None,
    error: str | None = None,
) -> None:
    session.add(
        ConsultationNotification(
            request_id=request_id,
            recipient_user_id=recipient.id,
            kind=kind,
            chat_id=recipient.telegram_id,
            message_id=message_id,
            status="SENT" if error is None else "FAILED",
            error=error,
        )
    )


async def claim(session: AsyncSession, request_id: int, manager: User) -> bool:
    """Atomic first-wins claim: NEW → IN_PROGRESS. False if someone was faster."""
    claimed = await session.scalar(
        update(ConsultationRequest)
        .where(ConsultationRequest.id == request_id, ConsultationRequest.status == "NEW")
        .values(status="IN_PROGRESS", claimed_by=manager.id, claimed_at=datetime.now(UTC))
        .returning(ConsultationRequest.id)
    )
    if claimed is None:
        return False
    req = await session.get(ConsultationRequest, request_id)
    await events.log_event(
        session,
        events.CONSULTATION_TAKEN,
        user_id=req.user_id if req else None,
        actor_user_id=manager.id,
        data={"request_id": request_id},
    )
    return True


async def complete(session: AsyncSession, request_id: int, manager: User, result: str) -> bool:
    """IN_PROGRESS → COMPLETED by the manager who claimed it. Result is then read-only."""
    if result not in RESULTS:
        raise ValueError(result)
    done = await session.scalar(
        update(ConsultationRequest)
        .where(
            ConsultationRequest.id == request_id,
            ConsultationRequest.status == "IN_PROGRESS",
            ConsultationRequest.claimed_by == manager.id,
        )
        .values(status="COMPLETED", result=result, completed_at=datetime.now(UTC))
        .returning(ConsultationRequest.user_id)
    )
    if done is None:
        return False
    await events.log_event(
        session,
        events.CONSULTATION_COMPLETED,
        user_id=done,
        actor_user_id=manager.id,
        data={"request_id": request_id, "result": result},
    )
    return True


async def notifications_for(
    session: AsyncSession, request_id: int
) -> list[ConsultationNotification]:
    return list(
        (
            await session.scalars(
                select(ConsultationNotification).where(
                    ConsultationNotification.request_id == request_id,
                    ConsultationNotification.status == "SENT",
                )
            )
        ).all()
    )


async def manager_requests(
    session: AsyncSession, manager_id: int, active: bool, limit: int = 20
) -> list[ConsultationRequest]:
    statuses = ("IN_PROGRESS",) if active else ("COMPLETED", "CLOSED_BY_ADMIN")
    order = (
        ConsultationRequest.claimed_at.desc() if active else ConsultationRequest.completed_at.desc()
    )
    return list(
        (
            await session.scalars(
                select(ConsultationRequest)
                .where(
                    ConsultationRequest.claimed_by == manager_id,
                    ConsultationRequest.status.in_(statuses),
                )
                .order_by(order.nulls_last(), ConsultationRequest.id.desc())
                .limit(limit)
            )
        ).all()
    )
