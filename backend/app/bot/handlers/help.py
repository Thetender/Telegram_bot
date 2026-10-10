"""Help section, marketing preference and personal-data deletion request
(FR §8, §12, §13.2; UI/UX §9, §14)."""

from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import keyboards as kb
from app.bot import texts as t
from app.config import Settings
from app.models import User
from app.services import campaigns as campaigns_svc
from app.services import legal, privacy
from app.services import users as users_svc

log = logging.getLogger(__name__)

STATIC_SECTIONS = {
    "faq": t.HELP_FAQ,
    "about": t.HELP_ABOUT,
    "tariffs": t.HELP_TARIFFS,
}


async def show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
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


async def notify_admins_about_deletion(
    bot: Bot, session: AsyncSession, settings: Settings, user: User
) -> None:
    """Admins with system notifications ON learn about the new request (FR §12)."""
    from app.services.consultations import notification_targets

    _, admins = await notification_targets(session)
    link = f"{settings.public_base_url}/admin/settings#privacy" if settings.public_base_url else ""
    text = t.PRIVACY_ADMIN_NOTICE.format(
        name=html.escape(user.name or "—"), phone=html.escape(user.phone or "—"), link=link
    )
    for admin in admins:
        try:
            await bot.send_message(admin.telegram_id, text)
        except TelegramAPIError as exc:
            log.info("Privacy notice to admin %s not sent: %s", admin.id, exc)


def create_router() -> Router:
    router = Router(name="help")
    router.callback_query.filter(filters.registered)

    @router.callback_query(kb.HelpCb.filter())
    async def on_help_section(
        callback: CallbackQuery,
        callback_data: kb.HelpCb,
        session: AsyncSession,
        user: User,
        settings: Settings,
        roles: set[str],
    ) -> None:
        section = callback_data.section
        if section == "home":
            await callback.answer()
            await callback.bot.send_message(
                callback.from_user.id, t.MAIN_MENU, reply_markup=kb.main_menu(roles)
            )
            return
        if section == "menu":
            versions = await legal.get_active_versions(session)
            await show(callback, t.HELP_TITLE, kb.help_menu(versions))
        elif section in STATIC_SECTIONS:
            await show(callback, STATIC_SECTIONS[section], kb.back_to_help())
        elif section == "consult":
            from app.bot.handlers.consultation import show_consultation

            await callback.answer()
            await show_consultation(callback.bot, callback.from_user.id, session, settings, user)
            return
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

    @router.callback_query(F.data.startswith(f"{campaigns_svc.UNSUBSCRIBE_PREFIX}:"))
    async def on_unsubscribe(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
        """«🔕 Відписатися від розсилок» under a campaign message."""
        await users_svc.set_marketing_opt_out(session, user, opt_out=True)
        await callback.answer(t.UNSUBSCRIBED, show_alert=True)
        msg = callback.message
        if isinstance(msg, Message) and msg.reply_markup is not None:
            # Keep the campaign's own button, drop «Відписатися».
            rows = [
                row
                for row in msg.reply_markup.inline_keyboard
                if not any((b.callback_data or "").startswith("mu:") for b in row)
            ]
            try:
                await msg.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
                )
            except TelegramAPIError:
                pass

    @router.callback_query(kb.LegalCb.filter(F.action == "accept"))
    async def on_legal_accept(
        callback: CallbackQuery, session: AsyncSession, user: User, roles: set[str]
    ) -> None:
        await legal.accept_pending(session, user.id)
        await show(callback, t.LEGAL_ACCEPTED_TEXT, None)
        await callback.answer()
        await callback.bot.send_message(
            callback.from_user.id, t.MAIN_MENU, reply_markup=kb.main_menu(roles)
        )

    @router.callback_query(kb.PrivacyCb.filter())
    async def on_privacy(
        callback: CallbackQuery,
        callback_data: kb.PrivacyCb,
        session: AsyncSession,
        user: User,
        settings: Settings,
    ) -> None:
        if callback_data.action != "send":
            await callback.answer(t.STALE_ACTION)
            return
        _, created = await privacy.create_deletion_request(session, user)
        text = t.DELETE_REQUEST_CREATED if created else t.DELETE_REQUEST_EXISTS
        await show(callback, text, kb.back_to_help())
        await callback.answer()
        if created:
            await notify_admins_about_deletion(callback.bot, session, settings, user)

    return router
