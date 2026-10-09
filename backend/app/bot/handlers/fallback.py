"""Catch-all handlers. Must be included last."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot import texts as t
from app.models import User


def create_router() -> Router:
    router = Router(name="fallback")

    @router.message()
    async def on_any_message(
        message: Message, session: AsyncSession, user: User | None, roles: set[str]
    ) -> None:
        if user is None:
            await message.answer(t.REGISTRATION_REQUIRED, reply_markup=kb.share_phone())
            return
        await message.answer(t.UNKNOWN_INPUT, reply_markup=kb.main_menu(roles))

    @router.callback_query()
    async def on_any_callback(callback: CallbackQuery, user: User | None) -> None:
        if user is None:
            await callback.answer(t.REGISTRATION_REQUIRED, show_alert=True)
            return
        await callback.answer(t.STALE_ACTION)

    return router
