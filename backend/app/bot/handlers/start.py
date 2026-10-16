"""First launch: one welcome message and «Почати» (no phone, no documents to
open). The phone is asked later, only for a monitoring or a consultation."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot import phone as phone_flow
from app.bot import texts as t
from app.config import Settings
from app.models import User
from app.services import access, events, legal, links
from app.services import users as users_svc
from app.services.phone import normalize_phone


async def welcome_text(session: AsyncSession) -> str:
    versions = await legal.get_active_versions(session)
    terms = versions.get(legal.TERMS)
    if terms is None:
        rules = t.WELCOME_RULES
    else:
        url = links.with_utm(terms.public_url, links.MENU, "terms")
        rules = f'<a href="{html.escape(url)}">{t.WELCOME_RULES}</a>'
    return t.WELCOME.format(rules=rules)


async def send_welcome(bot: Bot, chat_id: int, session: AsyncSession) -> None:
    await bot.send_message(
        chat_id,
        await welcome_text(session),
        reply_markup=kb.welcome(),
        disable_web_page_preview=True,
    )


async def send_registration_screen(message: Message, session: AsyncSession) -> None:
    """Kept for callers that greet unknown visitors."""
    assert message.bot is not None
    await send_welcome(message.bot, message.chat.id, session)


def create_router() -> Router:
    router = Router(name="start")

    @router.message(CommandStart())
    async def on_start(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
        user: User | None,
        roles: set[str],
        command: CommandObject,
    ) -> None:
        await state.clear()
        source = links.acquisition_source(command.args)
        if user is not None:
            await events.log_event(
                session, events.BOT_STARTED, user_id=user.id, data={"source": source}
            )
            await message.answer(t.MAIN_MENU, reply_markup=kb.main_menu(roles))
            return
        await events.log_event(
            session,
            events.BOT_STARTED,
            data={
                "telegram_id": message.from_user.id if message.from_user else None,
                "source": source,
            },
        )
        if source:
            # Kept until «Почати», then saved in the profile.
            await state.update_data(acquisition_source=source)
        await send_registration_screen(message, session)

    @router.callback_query(kb.BeginCb.filter())
    async def on_begin(
        callback: CallbackQuery,
        session: AsyncSession,
        state: FSMContext,
        user: User | None,
        roles: set[str],
    ) -> None:
        """«Почати»: the rules are accepted, the account exists, the menu opens."""
        sender = callback.from_user
        await callback.answer()
        if user is None:
            reg_user, created = await users_svc.register_user(
                session,
                telegram_id=sender.id,
                phone=None,
                username=sender.username,
                name=users_svc.display_name(sender.first_name, sender.last_name),
            )
            if not created and reg_user.anonymized_at is not None:
                # Returning after a completed personal-data deletion: a fresh start.
                reg_user.anonymized_at = None
                reg_user.registered_at = datetime.now(UTC)
                await events.log_event(session, events.USER_REGISTERED, user_id=reg_user.id)
                created = True
            source = (await state.get_data()).get("acquisition_source")
            if created and source and reg_user.acquisition_source is None:
                reg_user.acquisition_source = links.acquisition_source(source)
            versions = await legal.get_active_versions(session)
            await legal.record_acceptance(session, reg_user.id, [v.id for v in versions.values()])
            await state.clear()
            roles = await users_svc.get_roles(session, reg_user.id)
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001 - the button is cosmetic
                pass
        await callback.bot.send_message(sender.id, t.MAIN_MENU, reply_markup=kb.main_menu(roles))

    @router.message(F.text.in_({t.BTN_CANCEL_PHONE, t.BTN_BACK_TO_SEARCH_PHONE}))
    async def on_cancel_phone(
        message: Message,
        state: FSMContext,
        roles: set[str],
        session: AsyncSession,
        user: User | None,
    ) -> None:
        """Back from the phone request: to the search (monitoring) or the menu."""
        await state.clear()
        await message.answer(t.MAIN_MENU, reply_markup=kb.main_menu(roles))
        if message.text == t.BTN_BACK_TO_SEARCH_PHONE and user is not None:
            from app.bot.handlers.search import render_screen
            from app.services import search as search_svc

            text, markup = render_screen(await search_svc.get_draft(session, user.id))
            await message.answer(text, reply_markup=markup)

    @router.message(F.contact)
    async def on_contact(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
        user: User | None,
        roles: set[str],
        settings: Settings,
    ) -> None:
        contact = message.contact
        sender = message.from_user
        assert contact is not None and sender is not None

        # Only the sender's own contact is accepted (FR §3.1).
        if contact.user_id is None or contact.user_id != sender.id:
            await message.answer(t.FOREIGN_CONTACT, reply_markup=kb.share_phone())
            return
        try:
            phone = normalize_phone(contact.phone_number)
        except ValueError:
            await message.answer(t.INVALID_PHONE, reply_markup=kb.share_phone())
            return

        pending = await state.get_data()
        reg_user, created = await users_svc.register_user(
            session,
            telegram_id=sender.id,
            phone=phone,
            username=sender.username,
            name=users_svc.display_name(sender.first_name, sender.last_name),
        )
        if not created and reg_user.anonymized_at is not None:
            reg_user.anonymized_at = None
            reg_user.registered_at = datetime.now(UTC)
            await events.log_event(session, events.USER_REGISTERED, user_id=reg_user.id)
            created = True
        if created:
            source = pending.get("acquisition_source")
            if source and reg_user.acquisition_source is None:
                reg_user.acquisition_source = links.acquisition_source(source)
            versions = await legal.get_active_versions(session)
            await legal.record_acceptance(session, reg_user.id, [v.id for v in versions.values()])
        await state.clear()

        if await access.apply_phone_block(session, reg_user):
            # The number belongs to a blocked user: no access for this account either.
            await message.answer(
                t.ACCESS_BLOCKED.format(phone=settings.consultation_phone),
                reply_markup=ReplyKeyboardRemove(),
            )
            reg_user.access_block_notified_at = datetime.now(UTC)
            return

        current_roles = await users_svc.get_roles(session, reg_user.id)
        await message.answer(t.PHONE_SAVED, reply_markup=kb.main_menu(current_roles))
        # Continue what the phone was asked for.
        after = pending.get("after_phone")
        bot = message.bot
        assert bot is not None
        if after == phone_flow.AFTER_MONITORING and pending.get("snap"):
            from app.bot.handlers.monitorings import ask_monitoring_name

            await ask_monitoring_name(bot, session, state, reg_user, int(pending["snap"]))
        elif after == phone_flow.AFTER_CONSULTATION:
            from app.bot.handlers.consultation import show_consultation

            await show_consultation(bot, sender.id, session, settings, reg_user)

    return router
