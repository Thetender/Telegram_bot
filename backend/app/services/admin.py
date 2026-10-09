"""Queries and actions behind the Admin Dashboard (FR §11)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    AdminInvitation,
    AdminSettings,
    ConsultationRequest,
    Event,
    LegalDocument,
    LegalDocumentVersion,
    ManagerSettings,
    PrivacyRequest,
    User,
    UserRole,
)
from app.services import events
from app.services.phone import normalize_phone

PAGE_SIZE = 50


# ---------------- Overview ----------------


@dataclass
class Overview:
    period_days: int
    users_total: int
    users_new: int
    searches: int
    consultations: int
    consultations_new: int
    series: list[tuple[date, int, int]]  # (day, registrations, searches)


async def overview(session: AsyncSession, period_days: int, tz: str) -> Overview:
    since = datetime.now(UTC) - timedelta(days=period_days)
    active_users = User.anonymized_at.is_(None)
    users_total = await session.scalar(select(func.count()).select_from(User).where(active_users))
    users_new = await session.scalar(
        select(func.count()).select_from(User).where(active_users, User.registered_at >= since)
    )
    searches = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.event_type == events.SEARCH_COMPLETED, Event.created_at >= since)
    )
    consultations = await session.scalar(
        select(func.count())
        .select_from(ConsultationRequest)
        .where(ConsultationRequest.created_at >= since)
    )
    consultations_new = await session.scalar(
        select(func.count())
        .select_from(ConsultationRequest)
        .where(ConsultationRequest.status == "NEW")
    )

    local_day = cast(func.timezone(tz, Event.created_at), Date)
    rows = await session.execute(
        select(local_day, Event.event_type, func.count())
        .where(
            Event.created_at >= since,
            Event.event_type.in_([events.USER_REGISTERED, events.SEARCH_COMPLETED]),
        )
        .group_by(local_day, Event.event_type)
    )
    per_day: dict[date, list[int]] = {}
    for day, etype, count in rows:
        bucket = per_day.setdefault(day, [0, 0])
        bucket[0 if etype == events.USER_REGISTERED else 1] += count
    today = datetime.now(ZoneInfo(tz)).date()
    series = []
    for i in range(period_days - 1, -1, -1):
        d = today - timedelta(days=i)
        reg, srch = per_day.get(d, [0, 0])
        series.append((d, reg, srch))
    return Overview(
        period_days=period_days,
        users_total=users_total or 0,
        users_new=users_new or 0,
        searches=searches or 0,
        consultations=consultations or 0,
        consultations_new=consultations_new or 0,
        series=series,
    )


# ---------------- Users ----------------


def _user_search_clause(q: str):
    q = q.strip().lstrip("@")
    like = f"%{q}%"
    clauses = [User.name.ilike(like), User.username.ilike(like)]
    digits = "".join(ch for ch in q if ch.isdigit())
    if digits:
        clauses.append(User.phone.ilike(f"%{digits}%"))
        if digits == q:
            clauses.append(User.telegram_id == int(digits))
    return or_(*clauses)


async def list_users(
    session: AsyncSession,
    q: str = "",
    registered_from: date | None = None,
    registered_to: date | None = None,
    active_days: int | None = None,
    page: int = 1,
) -> tuple[list[tuple[User, set[str]]], int]:
    stmt = select(User).where(User.anonymized_at.is_(None))
    if q.strip():
        stmt = stmt.where(_user_search_clause(q))
    if registered_from:
        stmt = stmt.where(cast(User.registered_at, Date) >= registered_from)
    if registered_to:
        stmt = stmt.where(cast(User.registered_at, Date) <= registered_to)
    if active_days:
        stmt = stmt.where(User.last_activity_at >= datetime.now(UTC) - timedelta(days=active_days))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users = (
        await session.scalars(
            stmt.order_by(User.registered_at.desc(), User.id.desc())
            .offset((max(page, 1) - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    roles = await roles_for(session, [u.id for u in users])
    return [(u, roles.get(u.id, set())) for u in users], total


async def roles_for(session: AsyncSession, user_ids: list[int]) -> dict[int, set[str]]:
    if not user_ids:
        return {}
    result: dict[int, set[str]] = {}
    for uid, role in await session.execute(
        select(UserRole.user_id, UserRole.role).where(UserRole.user_id.in_(user_ids))
    ):
        result.setdefault(uid, set()).add(role)
    return result


async def find_registered(session: AsyncSession, query: str, limit: int = 10) -> list[User]:
    """Search registered bot users by username or phone (admin/manager pickers)."""
    query = query.strip()
    if not query:
        return []
    clauses = []
    name = query.lstrip("@")
    if name:
        clauses.append(User.username.ilike(name))
    try:
        clauses.append(User.phone == normalize_phone(query))
    except ValueError:
        pass
    digits = "".join(ch for ch in query if ch.isdigit())
    if len(digits) >= 5:
        clauses.append(User.phone.ilike(f"%{digits}%"))
    if not clauses:
        return []
    return list(
        (
            await session.scalars(
                select(User)
                .where(User.anonymized_at.is_(None), or_(*clauses))
                .order_by(User.id)
                .limit(limit)
            )
        ).all()
    )


@dataclass
class UserProfile:
    user: User
    roles: set[str]
    searches: int
    consultations: list[ConsultationRequest]
    timeline: list[Event]
    actors: dict[int, User]


async def user_profile(session: AsyncSession, user_id: int) -> UserProfile | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    roles = (await roles_for(session, [user.id])).get(user.id, set())
    searches = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.user_id == user.id, Event.event_type == events.SEARCH_COMPLETED)
    )
    consultations = (
        await session.scalars(
            select(ConsultationRequest)
            .where(ConsultationRequest.user_id == user.id)
            .order_by(ConsultationRequest.created_at.desc())
        )
    ).all()
    timeline = (
        await session.scalars(
            select(Event)
            .where(or_(Event.user_id == user.id, Event.actor_user_id == user.id))
            .order_by(Event.created_at.desc(), Event.id.desc())
            .limit(100)
        )
    ).all()
    actor_ids = {e.actor_user_id for e in timeline if e.actor_user_id}
    actors = {}
    if actor_ids:
        actors = {
            u.id: u for u in await session.scalars(select(User).where(User.id.in_(actor_ids)))
        }
    return UserProfile(
        user=user,
        roles=roles,
        searches=searches or 0,
        consultations=list(consultations),
        timeline=list(timeline),
        actors=actors,
    )


# ---------------- Consultations ----------------

CONSULTATION_TABS = {
    "all": None,
    "new": ("NEW",),
    "progress": ("IN_PROGRESS",),
    "done": ("COMPLETED", "CLOSED_BY_ADMIN"),
}


async def list_consultations(
    session: AsyncSession, tab: str, page: int = 1
) -> tuple[list[tuple[ConsultationRequest, User | None]], int, dict[str, int]]:
    statuses = CONSULTATION_TABS.get(tab)
    stmt = select(ConsultationRequest)
    if statuses:
        stmt = stmt.where(ConsultationRequest.status.in_(statuses))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    reqs = (
        await session.scalars(
            stmt.order_by(ConsultationRequest.created_at.desc())
            .offset((max(page, 1) - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    manager_ids = {r.claimed_by for r in reqs if r.claimed_by}
    managers = {}
    if manager_ids:
        managers = {
            u.id: u for u in await session.scalars(select(User).where(User.id.in_(manager_ids)))
        }
    counts = dict(
        (
            await session.execute(
                select(ConsultationRequest.status, func.count()).group_by(
                    ConsultationRequest.status
                )
            )
        ).all()
    )
    tab_counts = {
        "all": sum(counts.values()),
        "new": counts.get("NEW", 0),
        "progress": counts.get("IN_PROGRESS", 0),
        "done": counts.get("COMPLETED", 0) + counts.get("CLOSED_BY_ADMIN", 0),
    }
    return [(r, managers.get(r.claimed_by)) for r in reqs], total, tab_counts


async def close_consultation(session: AsyncSession, request_id: int, admin: User) -> bool:
    """NEW / IN_PROGRESS → CLOSED_BY_ADMIN, without an INTERESTED/DECLINED result."""
    row = (
        await session.execute(
            update(ConsultationRequest)
            .where(
                ConsultationRequest.id == request_id,
                ConsultationRequest.status.in_(("NEW", "IN_PROGRESS")),
            )
            .values(status="CLOSED_BY_ADMIN", closed_by=admin.id, closed_at=datetime.now(UTC))
            .returning(ConsultationRequest.user_id, ConsultationRequest.status)
        )
    ).first()
    if row is None:
        return False
    await events.log_event(
        session,
        events.CONSULTATION_CLOSED_BY_ADMIN,
        user_id=row[0],
        actor_user_id=admin.id,
        data={"request_id": request_id},
    )
    return True


async def correct_result(
    session: AsyncSession, request_id: int, admin: User, new_result: str
) -> bool:
    if new_result not in ("INTERESTED", "DECLINED"):
        raise ValueError(new_result)
    req = await session.get(ConsultationRequest, request_id, with_for_update=True)
    if req is None or req.status != "COMPLETED" or req.result == new_result:
        return False
    previous = req.result
    req.result = new_result
    await events.log_event(
        session,
        events.CONSULTATION_RESULT_CORRECTED,
        user_id=req.user_id,
        actor_user_id=admin.id,
        data={"request_id": request_id, "previous": previous, "new": new_result},
    )
    return True


# ---------------- Staff (admins / managers) ----------------


@dataclass
class StaffRow:
    user: User
    notifications_enabled: bool


async def staff(session: AsyncSession, role: str) -> list[StaffRow]:
    settings_model = AdminSettings if role == ROLE_ADMIN else ManagerSettings
    flag = (
        AdminSettings.system_notifications_enabled
        if role == ROLE_ADMIN
        else ManagerSettings.consultation_notifications_enabled
    )
    rows = await session.execute(
        select(User, flag)
        .join(UserRole, and_(UserRole.user_id == User.id, UserRole.role == role))
        .outerjoin(settings_model, settings_model.user_id == User.id)
        .order_by(User.name, User.id)
    )
    return [StaffRow(user=u, notifications_enabled=enabled is not False) for u, enabled in rows]


async def set_notifications(
    session: AsyncSession, user_id: int, role: str, enabled: bool, actor: User
) -> None:
    if role == ROLE_ADMIN:
        stmt = insert(AdminSettings).values(user_id=user_id, system_notifications_enabled=enabled)
        stmt = stmt.on_conflict_do_update(
            index_elements=[AdminSettings.user_id],
            set_={"system_notifications_enabled": enabled},
        )
    else:
        stmt = insert(ManagerSettings).values(
            user_id=user_id, consultation_notifications_enabled=enabled
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ManagerSettings.user_id],
            set_={"consultation_notifications_enabled": enabled},
        )
    await session.execute(stmt)
    await events.log_event(
        session,
        events.SETTINGS_CHANGED,
        user_id=user_id,
        actor_user_id=actor.id,
        data={"setting": f"{role.lower()}_notifications", "new": enabled},
    )


async def create_invitation(
    session: AsyncSession, invited: User, inviter: User
) -> tuple[AdminInvitation | None, str]:
    """Returns (invitation, status) where status is created | already_admin | pending."""
    roles = (await roles_for(session, [invited.id])).get(invited.id, set())
    if ROLE_ADMIN in roles:
        return None, "already_admin"
    new_id = await session.scalar(
        insert(AdminInvitation)
        .values(invited_user_id=invited.id, invited_by=inviter.id, status="PENDING")
        .on_conflict_do_nothing(
            index_elements=[AdminInvitation.invited_user_id],
            index_where=AdminInvitation.status == "PENDING",
        )
        .returning(AdminInvitation.id)
    )
    if new_id is None:
        existing = await session.scalar(
            select(AdminInvitation).where(
                AdminInvitation.invited_user_id == invited.id,
                AdminInvitation.status == "PENDING",
            )
        )
        return existing, "pending"
    await events.log_event(
        session,
        events.ADMIN_INVITED,
        user_id=invited.id,
        actor_user_id=inviter.id,
        data={"invitation_id": new_id},
    )
    return await session.get(AdminInvitation, new_id), "created"


async def respond_invitation(
    session: AsyncSession, invitation_id: int, user: User, accept: bool
) -> str:
    """Invited user's answer in the bot. Returns accepted | declined | stale."""
    from app.services.users import grant_role

    row = (
        await session.execute(
            update(AdminInvitation)
            .where(
                AdminInvitation.id == invitation_id,
                AdminInvitation.invited_user_id == user.id,
                AdminInvitation.status == "PENDING",
            )
            .values(status="ACCEPTED" if accept else "DECLINED", responded_at=datetime.now(UTC))
            .returning(AdminInvitation.invited_by)
        )
    ).first()
    if row is None:
        return "stale"
    if accept:
        await grant_role(session, user, ROLE_ADMIN, actor_user_id=row[0])
        await events.log_event(
            session, events.ADMIN_INVITATION_ACCEPTED, user_id=user.id, actor_user_id=user.id
        )
        return "accepted"
    await events.log_event(
        session, events.ADMIN_INVITATION_DECLINED, user_id=user.id, actor_user_id=user.id
    )
    return "declined"


async def pending_invitations(session: AsyncSession) -> list[tuple[AdminInvitation, User]]:
    rows = await session.execute(
        select(AdminInvitation, User)
        .join(User, User.id == AdminInvitation.invited_user_id)
        .where(AdminInvitation.status == "PENDING")
        .order_by(AdminInvitation.created_at.desc())
    )
    return list(rows.tuples())


async def cancel_invitation(session: AsyncSession, invitation_id: int) -> None:
    await session.execute(
        update(AdminInvitation)
        .where(AdminInvitation.id == invitation_id, AdminInvitation.status == "PENDING")
        .values(status="CANCELLED", responded_at=datetime.now(UTC))
    )


# ---------------- Legal documents ----------------


async def legal_versions(session: AsyncSession) -> dict[str, list[LegalDocumentVersion]]:
    rows = await session.execute(
        select(LegalDocument.doc_type, LegalDocumentVersion)
        .join(LegalDocumentVersion, LegalDocumentVersion.document_id == LegalDocument.id)
        .order_by(LegalDocumentVersion.created_at.desc())
    )
    result: dict[str, list[LegalDocumentVersion]] = {"TERMS": [], "PRIVACY": []}
    for doc_type, version in rows:
        result.setdefault(doc_type, []).append(version)
    return result


def file_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def change_public_url(session: AsyncSession, version_id: int, url: str, actor: User) -> bool:
    version = await session.get(LegalDocumentVersion, version_id)
    if version is None or version.public_url == url:
        return False
    previous = version.public_url
    version.public_url = url
    await events.log_event(
        session,
        events.LEGAL_URL_CHANGED,
        actor_user_id=actor.id,
        data={"version_id": version_id, "previous": previous, "new": url},
    )
    return True


# ---------------- Privacy ----------------


async def privacy_requests(session: AsyncSession) -> list[tuple[PrivacyRequest, User]]:
    rows = await session.execute(
        select(PrivacyRequest, User)
        .join(User, User.id == PrivacyRequest.user_id)
        .order_by(PrivacyRequest.created_at.desc())
        .limit(200)
    )
    return list(rows.tuples())


def event_summary(event: Event) -> dict[str, Any]:
    return dict(event.event_data or {})


__all__ = ["ROLE_ADMIN", "ROLE_MANAGER"]
