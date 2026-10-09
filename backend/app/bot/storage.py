"""aiogram FSM storage backed by PostgreSQL (table bot_sessions).

Conversation state survives restarts and deployments. No Redis needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BotSession


def _resolve_state(value: StateType) -> str | None:
    if value is None:
        return None
    if isinstance(value, State):
        return value.state
    return str(value)


class PostgresStorage(BaseStorage):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _upsert(self, user_id: int, **values: Any) -> None:
        stmt = insert(BotSession).values(telegram_user_id=user_id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[BotSession.telegram_user_id],
            set_={**values, "version": BotSession.version + 1},
        )
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await self._upsert(key.user_id, state=_resolve_state(state))

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._sf() as session:
            return await session.scalar(
                select(BotSession.state).where(BotSession.telegram_user_id == key.user_id)
            )

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._upsert(key.user_id, state_data=dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._sf() as session:
            data = await session.scalar(
                select(BotSession.state_data).where(BotSession.telegram_user_id == key.user_id)
            )
        return dict(data or {})

    async def close(self) -> None:
        return None
