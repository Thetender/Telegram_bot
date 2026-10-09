from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ROLE_ADMIN, AdminSettings, ManagerSettings, User, UserRole
from app.services import events


def display_name(first_name: str | None, last_name: str | None) -> str | None:
    name = " ".join(p for p in (first_name, last_name) if p)
    return name or None


async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def register_user(
    session: AsyncSession,
    telegram_id: int,
    phone: str,
    username: str | None,
    name: str | None,
) -> tuple[User, bool]:
    """Create the user (or refresh phone/profile of an existing one).

    Returns (user, created). Safe against concurrent duplicates thanks to
    the UNIQUE(telegram_id) constraint.
    """
    now = datetime.now(UTC)
    stmt = (
        insert(User)
        .values(
            telegram_id=telegram_id,
            phone=phone,
            username=username,
            name=name,
            registered_at=now,
            last_activity_at=now,
        )
        .on_conflict_do_nothing(index_elements=[User.telegram_id])
        .returning(User.id)
    )
    new_id = await session.scalar(stmt)
    user = await get_by_telegram_id(session, telegram_id)
    assert user is not None
    if new_id is None:
        # Existing user shared the contact again: refresh contact data.
        user.phone = phone
        user.username = username
        user.name = name
        return user, False
    await events.log_event(session, events.USER_REGISTERED, user_id=user.id)
    return user, True


async def get_roles(session: AsyncSession, user_id: int) -> set[str]:
    rows = await session.scalars(select(UserRole.role).where(UserRole.user_id == user_id))
    return set(rows)


async def grant_role(
    session: AsyncSession, user: User, role: str, actor_user_id: int | None
) -> bool:
    """Grant a role. Returns False if the user already had it."""
    res = await session.scalar(
        insert(UserRole)
        .values(user_id=user.id, role=role, granted_by=actor_user_id)
        .on_conflict_do_nothing(constraint="uq_user_roles_user_role")
        .returning(UserRole.id)
    )
    if res is None:
        return False
    if role == ROLE_ADMIN:
        await session.execute(
            insert(AdminSettings).values(user_id=user.id).on_conflict_do_nothing()
        )
    else:
        await session.execute(
            insert(ManagerSettings).values(user_id=user.id).on_conflict_do_nothing()
        )
    await events.log_event(
        session,
        events.ROLE_GRANTED,
        user_id=user.id,
        actor_user_id=actor_user_id,
        data={"role": role, "previous": None, "new": role},
    )
    return True


async def count_admins(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(UserRole).where(UserRole.role == ROLE_ADMIN)
    ) or 0


async def set_marketing_opt_out(session: AsyncSession, user: User, opt_out: bool) -> None:
    previous = user.marketing_opt_out_at is not None
    if previous == opt_out:
        return
    user.marketing_opt_out_at = datetime.now(UTC) if opt_out else None
    await events.log_event(
        session,
        events.MARKETING_PREFERENCE_CHANGED,
        user_id=user.id,
        actor_user_id=user.id,
        data={"marketing_enabled": not opt_out},
    )
