"""Monitorings: The Tender is canonical, the local table is an index/cache.

Every change goes to The Tender first; the local row is synchronized from
the backend response (or a read-back). Nothing is reported as saved when
the API call failed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.thetender import ExternalMonitoring, TenderClient, TenderError
from app.models import Monitoring, MonitoringEditDraft, User
from app.services import events
from app.services.search_params import normalize

MAX_NAME_LEN = 100
MAX_PER_USER = 200


def tender_user_id(user: User) -> str:
    """user_id for The Tender: the Telegram user id as a string (API doc §3)."""
    return str(user.telegram_id)


async def _upsert(session: AsyncSession, user: User, ext: ExternalMonitoring) -> Monitoring:
    now = datetime.now(UTC)
    values = {
        "user_id": user.id,
        "external_id": ext.id,
        "name": ext.name[:255],
        "params": ext.params,
        "raw": ext.raw,
        "last_synced_at": now,
        "deleted_at": None,
    }
    await session.execute(
        insert(Monitoring)
        .values(**values)
        .on_conflict_do_update(index_elements=[Monitoring.external_id], set_=values)
    )
    row = await session.scalar(
        select(Monitoring)
        .where(Monitoring.external_id == ext.id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def active(session: AsyncSession, user_id: int) -> list[Monitoring]:
    return list(
        await session.scalars(
            select(Monitoring)
            .where(Monitoring.user_id == user_id, Monitoring.deleted_at.is_(None))
            .order_by(Monitoring.created_at, Monitoring.id)
        )
    )


async def count_active(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Monitoring)
            .where(Monitoring.user_id == user_id, Monitoring.deleted_at.is_(None))
        )
        or 0
    )


async def sync_user(session: AsyncSession, tender: TenderClient, user: User) -> list[Monitoring]:
    """Load the canonical list from The Tender and refresh the local index."""
    remote = await tender.list_monitorings(tender_user_id(user))
    seen = set()
    for ext in remote:
        await _upsert(session, user, ext)
        seen.add(ext.id)
    now = datetime.now(UTC)
    for row in await active(session, user.id):
        if row.external_id not in seen:
            row.deleted_at = now  # removed on the backend side
    return await active(session, user.id)


async def get_owned(session: AsyncSession, monitoring_id: int, user_id: int) -> Monitoring | None:
    row = await session.get(Monitoring, monitoring_id)
    if row is None or row.user_id != user_id or row.deleted_at is not None:
        return None
    return row


async def create(
    session: AsyncSession, tender: TenderClient, user: User, name: str, params: dict[str, Any]
) -> Monitoring:
    """Create on The Tender. A lost response is reconciled through the
    user's monitoring list instead of blindly creating a second copy."""
    clean = normalize(params)
    known = {row.external_id for row in await active(session, user.id)}
    try:
        ext = await tender.create_monitoring(tender_user_id(user), name, clean)
    except TenderError as exc:
        if exc.kind != "ambiguous":
            raise
        ext = await _reconcile_create(tender, user, name, clean, known)
        if ext is None:
            raise
    row = await _upsert(session, user, ext)
    await events.log_event(
        session,
        events.MONITOR_CREATED,
        user_id=user.id,
        data={"monitoring_id": row.id, "external_id": ext.id},
    )
    return row


async def _reconcile_create(
    tender: TenderClient, user: User, name: str, params: dict[str, Any], known: set[str]
) -> ExternalMonitoring | None:
    try:
        remote = await tender.list_monitorings(tender_user_id(user))
    except TenderError:
        return None
    for ext in remote:
        if ext.id not in known and ext.name == name and ext.params == params:
            return ext
    return None


async def update(
    session: AsyncSession,
    tender: TenderClient,
    user: User,
    row: Monitoring,
    name: str,
    params: dict[str, Any],
) -> Monitoring:
    ext = await tender.update_monitoring(row.external_id, tender_user_id(user), name, params)
    row = await _upsert(session, user, ext)
    await events.log_event(
        session, events.MONITOR_UPDATED, user_id=user.id, data={"monitoring_id": row.id}
    )
    return row


async def delete(session: AsyncSession, tender: TenderClient, user: User, row: Monitoring) -> None:
    try:
        await tender.delete_monitoring(row.external_id)
    except TenderError as exc:
        if exc.kind != "not_found":  # already gone on the backend: fine
            raise
    row.deleted_at = datetime.now(UTC)
    await events.log_event(
        session, events.MONITOR_DELETED, user_id=user.id, data={"monitoring_id": row.id}
    )


# ---------- edit drafts (independent from the search draft) ----------


async def start_edit(session: AsyncSession, user_id: int, row: Monitoring) -> MonitoringEditDraft:
    values = {"monitoring_id": row.id, "name": row.name, "params": normalize(row.params)}
    await session.execute(
        insert(MonitoringEditDraft)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=[MonitoringEditDraft.user_id],
            set_={**values, "version": MonitoringEditDraft.version + 1},
        )
    )
    draft = await get_edit(session, user_id)
    assert draft is not None
    return draft


async def get_edit(
    session: AsyncSession, user_id: int, lock: bool = False
) -> MonitoringEditDraft | None:
    stmt = (
        select(MonitoringEditDraft)
        .where(MonitoringEditDraft.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def update_edit(session: AsyncSession, user_id: int, **changes: Any) -> dict[str, Any]:
    draft = await get_edit(session, user_id, lock=True)
    if draft is None:
        return {}
    params = dict(draft.params)
    for key, value in changes.items():
        if value is None or value == [] or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    draft.params = normalize(params)
    draft.version += 1
    return dict(draft.params)


async def save_edit_params(session: AsyncSession, user_id: int, params: dict[str, Any]) -> None:
    draft = await get_edit(session, user_id, lock=True)
    if draft is not None:
        draft.params = normalize(params)
        draft.version += 1


async def rename_edit(session: AsyncSession, user_id: int, name: str) -> None:
    draft = await get_edit(session, user_id, lock=True)
    if draft is not None:
        draft.name = name
        draft.version += 1


async def clear_edit(session: AsyncSession, user_id: int) -> None:
    draft = await get_edit(session, user_id, lock=True)
    if draft is not None:
        await session.delete(draft)


def clean_name(text: str | None) -> str | None:
    """Valid monitoring name or None."""
    name = " ".join((text or "").split())
    if not name or name.startswith("/") or len(name) > MAX_NAME_LEN:
        return None
    return name


def suggested_name(params: dict[str, Any]) -> str:
    """Default name from the filters, e.g. «Продаж · Київ, Львівська область»."""
    from app.services.search_params import summary_lines

    parts = [value for _, value in summary_lines(params)][:3]
    name = " · ".join(parts) or "Мій моніторинг"
    return name if len(name) <= MAX_NAME_LEN else name[: MAX_NAME_LEN - 1] + "…"
