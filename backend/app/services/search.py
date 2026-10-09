from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SearchDraft, SearchSnapshot
from app.services.search_params import normalize


async def get_draft(session: AsyncSession, user_id: int, lock: bool = False) -> dict[str, Any]:
    stmt = select(SearchDraft).where(SearchDraft.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    draft = await session.scalar(stmt)
    return dict(draft.params) if draft else {}


async def save_draft(session: AsyncSession, user_id: int, params: dict[str, Any]) -> None:
    clean = normalize(params)
    await session.execute(
        insert(SearchDraft)
        .values(user_id=user_id, params=clean)
        .on_conflict_do_update(
            index_elements=[SearchDraft.user_id],
            set_={"params": clean, "version": SearchDraft.version + 1},
        )
    )


async def update_draft(session: AsyncSession, user_id: int, **changes: Any) -> dict[str, Any]:
    """Read-modify-write under a row lock. A value of None removes the field."""
    await session.execute(
        insert(SearchDraft).values(user_id=user_id, params={}).on_conflict_do_nothing()
    )
    params = await get_draft(session, user_id, lock=True)
    for key, value in changes.items():
        if value is None or value == [] or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    await save_draft(session, user_id, params)
    return normalize(params)


async def clear_draft(session: AsyncSession, user_id: int) -> None:
    await save_draft(session, user_id, {})


async def create_snapshot(
    session: AsyncSession, user_id: int, params: dict[str, Any], source: str = "SEARCH"
) -> SearchSnapshot:
    snap = SearchSnapshot(user_id=user_id, params=normalize(params), source=source)
    session.add(snap)
    await session.flush()
    return snap


async def get_snapshot(
    session: AsyncSession, snapshot_id: int, user_id: int
) -> SearchSnapshot | None:
    """Ownership enforced: another user's snapshot is treated as missing."""
    snap = await session.get(SearchSnapshot, snapshot_id)
    if snap is None or snap.user_id != user_id:
        return None
    return snap


async def mark_page_shown(session: AsyncSession, snapshot_id: int, page: int) -> bool:
    """Atomically claim the right to send `page`. False if it was already sent."""
    res = await session.execute(
        update(SearchSnapshot)
        .where(SearchSnapshot.id == snapshot_id, SearchSnapshot.pages_shown < page)
        .values(pages_shown=page)
        .returning(SearchSnapshot.id)
    )
    return res.scalar_one_or_none() is not None
