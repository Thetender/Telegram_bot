"""Help section, marketing preference and personal-data deletion request
(FR §8, §12, §13.2; UI/UX §9, §14)."""

from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import keyboards as kb
from app.bot import texts as t
from app.models import User
from app.services import legal, privacy
from app.services import users as users_svc

STATIC_SECTIONS = {
    "faq": t.HELP_FAQ,
    "about": t.HELP_ABOUT,
    "tariffs": t.HELP_TARIFFS,
}


async def show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Edit the callback's message in place; fall back to a new message."""
    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
    if callback.from_user:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


def create_router() -> Router:
    router = Router(name="help")
    router.callback_query.filter(filters.registered)

    @router.callback_query(kb.HelpCb.filter())
    async def on_help_section(
        callback: CallbackQuery,
        callback_data: kb.HelpCb,
        session: AsyncSession,
        user: User,
    ) -> None:
        section = callback_data.section
        if section == "menu":
            versions = await legal.get_active_versions(session)
            await show(callback, t.HELP_TITLE, kb.help_menu(versions))
        elif section in STATIC_SECTIONS:
            await show(callback, STATIC_SECTIONS[section], kb.back_to_help())
        elif section == "consult":
            await show(callback, t.coming_soon("Консультація"), kb.back_to_help())
        elif section == "settings":
            enabled = user.marketing_opt_out_at is None
            await show(callback, t.marketing_state(enabled), kb.marketing(enabled))
        elif section == "delete":
            await show(callback, t.DELETE_DATA_EXPLANATION, kb.delete_data())
        else:
            await callback.answer(t.STALE_ACTION)
            return
        await callback.answer()

    @router.callback_query(kb.MarketingCb.filter())
    async def on_marketing(
        callback: CallbackQuery,
        callback_data: kb.MarketingCb,
        session: AsyncSession,
        user: User,
    ) -> None:
        await users_svc.set_marketing_opt_out(session, user, opt_out=not callback_data.enable)
        enabled = user.marketing_opt_out_at is None
        await show(callback, t.marketing_state(enabled), kb.marketing(enabled))
        await callback.answer(t.SETTINGS_SAVED)

    @router.callback_query(kb.PrivacyCb.filter())
    async def on_privacy(
        callback: CallbackQuery,
        callback_data: kb.PrivacyCb,
        session: AsyncSession,
        user: User,
    ) -> None:
        if callback_data.action != "send":
            await callback.answer(t.STALE_ACTION)
            return
        _, created = await privacy.create_deletion_request(session, user)
        text = t.DELETE_REQUEST_CREATED if created else t.DELETE_REQUEST_EXISTS
        await show(callback, text, kb.back_to_help())
        await callback.answer()

    return router
