from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ReplyKeyboardRemove, TelegramObject, Update
from aiogram.types import User as TgUser
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import keyboards as kb
from app.bot import texts as t
from app.config import Settings
from app.models import ProcessedUpdate, User
from app.services import legal
from app.services import users as users_svc

log = logging.getLogger(__name__)


# Callbacks still available while new Terms/Privacy wait for confirmation:
# accept, marketing preference and the personal-data deletion request.
_GATE_ALLOWED_CALLBACKS = ("lg:", "mk:", "pd:", "h:settings", "h:delete")


def _allowed_during_legal_gate(event: Update) -> bool:
    cq = event.callback_query
    return cq is not None and (cq.data or "").startswith(_GATE_ALLOWED_CALLBACKS)


class UpdateContextMiddleware(BaseMiddleware):
    """Outer middleware for every Telegram update.

    1. Deduplicates by update_id (persisted before any side effect).
    2. Opens one DB session/transaction per update and commits at the end.
    3. Loads the registered user (or None) and their roles.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        assert isinstance(event, Update)
        async with self._sf() as session:
            first_time = await session.scalar(
                insert(ProcessedUpdate)
                .values(update_id=event.update_id)
                .on_conflict_do_nothing()
                .returning(ProcessedUpdate.update_id)
            )
            await session.commit()
            if first_time is None:
                log.info("Duplicate Telegram update %s ignored", event.update_id)
                return None

            tg_user: TgUser | None = data.get("event_from_user")
            user = None
            roles: set[str] = set()
            if tg_user is not None:
                user = await users_svc.get_by_telegram_id(session, tg_user.id)
                if user is not None and user.anonymized_at is not None:
                    user = None  # personal data deleted: behaves as a new visitor
                if user is not None:
                    user.last_activity_at = datetime.now(UTC)
                    if user.bot_blocked_at is not None:
                        user.bot_blocked_at = None  # user is talking to us again
                    if tg_user.username != user.username:
                        user.username = tg_user.username
                    roles = await users_svc.get_roles(session, user.id)

            if user is not None and user.is_access_blocked:
                # Access closed by an administrator: one short notice, then silence.
                await self._notify_blocked(data, user, session)
                await session.commit()
                return None

            if user is not None and not _allowed_during_legal_gate(event):
                pending = await legal.pending_reacceptance(session, user.id)
                if pending:
                    await self._show_legal_gate(data, event, user, pending)
                    await session.commit()
                    return None

            data["session"] = session
            data["user"] = user
            data["roles"] = roles
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result

    @staticmethod
    async def _show_legal_gate(
        data: dict[str, Any], event: Update, user: User, pending: list
    ) -> None:
        """New Terms/Privacy require confirmation: interactive functions wait
        until the user accepts (FR §3.3). Notifications keep working."""
        bot: Bot | None = data.get("bot")
        if bot is None:
            return
        if event.callback_query is not None:
            with contextlib.suppress(TelegramAPIError):
                await bot.answer_callback_query(event.callback_query.id)
        key = tuple(sorted({v.doc_type for v in pending}))
        text = t.LEGAL_GATE.format(
            docs=t.LEGAL_DOC_NAMES.get(key, t.LEGAL_DOC_NAMES[("PRIVACY", "TERMS")])
        )
        try:
            await bot.send_message(user.telegram_id, text, reply_markup=kb.legal_gate(pending))
        except TelegramAPIError as exc:
            log.info("Legal gate for %s not sent: %s", user.id, exc)

    @staticmethod
    async def _notify_blocked(data: dict[str, Any], user: User, session: AsyncSession) -> None:
        if user.access_block_notified_at is not None:
            return
        bot: Bot | None = data.get("bot")
        settings: Settings | None = data.get("settings")
        if bot is None:
            return
        phone = settings.consultation_phone if settings else ""
        try:
            await bot.send_message(
                user.telegram_id,
                t.ACCESS_BLOCKED.format(phone=phone),
                reply_markup=ReplyKeyboardRemove(),
            )
        except TelegramAPIError as exc:
            log.info("Blocked-access notice to %s not sent: %s", user.id, exc)
        user.access_block_notified_at = datetime.now(UTC)
