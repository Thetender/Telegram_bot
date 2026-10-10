"""Closing bot access for unwanted users (e.g. competitors).

A block applies to the Telegram account and to its phone number: any other
account with the same number (now or registered later) is blocked as well.
Staff (admins / managers) cannot be blocked — remove the role first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlockedPhone, ConsultationRequest, User, UserRole
from app.services import events

MAX_REASON_LEN = 500


class BlockResult(StrEnum):
    OK = "ok"
    STAFF = "staff"
    SELF = "self"
    ALREADY = "already"


async def is_phone_blocked(session: AsyncSession, phone: str | None) -> BlockedPhone | None:
    if not phone:
        return None
    return await session.get(BlockedPhone, phone)


async def _is_staff(session: AsyncSession, user_ids: list[int]) -> bool:
    found = await session.scalar(select(UserRole.id).where(UserRole.user_id.in_(user_ids)).limit(1))
    return found is not None


async def block_user(
    session: AsyncSession, target: User, admin: User, reason: str | None
) -> tuple[BlockResult, list[int]]:
    """Block the account and every account with the same phone.

    Returns (result, ids of consultation requests that were closed) so the
    caller can refresh the managers' Telegram messages."""
    if target.id == admin.id:
        return BlockResult.SELF, []
    if target.is_access_blocked:
        return BlockResult.ALREADY, []
    reason = (reason or "").strip()[:MAX_REASON_LEN] or None

    victims = [target]
    if target.phone:
        same_phone = await session.scalars(
            select(User).where(User.phone == target.phone, User.id != target.id)
        )
        victims += [u for u in same_phone if not u.is_access_blocked]
    if await _is_staff(session, [u.id for u in victims]):
        return BlockResult.STAFF, []

    now = datetime.now(UTC)
    for u in victims:
        u.access_blocked_at = now
        u.access_blocked_by = admin.id
        u.access_block_reason = reason
        u.access_block_notified_at = None
    if target.phone:
        await session.execute(
            insert(BlockedPhone)
            .values(phone=target.phone, reason=reason, created_by=admin.id)
            .on_conflict_do_nothing()
        )

    # Open consultation requests of blocked users are closed.
    closed = (
        await session.scalars(
            update(ConsultationRequest)
            .where(
                ConsultationRequest.user_id.in_([u.id for u in victims]),
                ConsultationRequest.status.in_(("NEW", "IN_PROGRESS")),
            )
            .values(status="CLOSED_BY_ADMIN", closed_by=admin.id, closed_at=now)
            .returning(ConsultationRequest.id)
        )
    ).all()

    for u in victims:
        await events.log_event(
            session,
            events.USER_ACCESS_BLOCKED,
            user_id=u.id,
            actor_user_id=admin.id,
            data={"reason": reason} if reason else {},
        )
    for request_id in closed:
        await events.log_event(
            session,
            events.CONSULTATION_CLOSED_BY_ADMIN,
            user_id=target.id,
            actor_user_id=admin.id,
            data={"request_id": request_id, "reason": "access_blocked"},
        )
    return BlockResult.OK, list(closed)


async def unblock_user(session: AsyncSession, target: User, admin: User) -> bool:
    """Unblock the account, its phone and every account with that phone."""
    if not target.is_access_blocked:
        return False
    victims = [target]
    if target.phone:
        victims += list(
            await session.scalars(
                select(User).where(
                    User.phone == target.phone,
                    User.id != target.id,
                    User.access_blocked_at.is_not(None),
                )
            )
        )
        await session.execute(delete(BlockedPhone).where(BlockedPhone.phone == target.phone))
    for u in victims:
        u.access_blocked_at = None
        u.access_blocked_by = None
        u.access_block_reason = None
        u.access_block_notified_at = None
        await events.log_event(
            session, events.USER_ACCESS_UNBLOCKED, user_id=u.id, actor_user_id=admin.id
        )
    return True


async def apply_phone_block(session: AsyncSession, user: User) -> bool:
    """Called at registration: block the account if its phone is blocked."""
    if user.is_access_blocked:
        return True
    blocked = await is_phone_blocked(session, user.phone)
    if blocked is None:
        return False
    user.access_blocked_at = datetime.now(UTC)
    user.access_blocked_by = blocked.created_by
    user.access_block_reason = blocked.reason
    await events.log_event(
        session,
        events.USER_ACCESS_BLOCKED,
        user_id=user.id,
        data={"by_phone": True},
    )
    return True
