"""/start and registration by shared phone contact (FR §3.1)."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot import texts as t
from app.models import User
from app.services import events, legal
from app.services import users as users_svc
from app.services.phone import normalize_phone


async def send_registration_screen(message: Message, session: AsyncSession) -> None:
    versions = await legal.get_active_versions(session)
    await message.answer(t.WELCOME, reply_markup=kb.legal_links(versions))
    await message.answer(t.SHARE_PHONE_PROMPT, reply_markup=kb.share_phone())


def create_router() -> Router:
    router = Router(name="start")

    @router.message(CommandStart())
    async def on_start(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
        user: User | None,
        roles: set[str],
    ) -> None:
        await state.clear()
        if user is not None:
            await events.log_event(session, events.BOT_STARTED, user_id=user.id)
            await message.answer(t.MAIN_MENU, reply_markup=kb.main_menu(roles))
            return
        await events.log_event(
            session,
            events.BOT_STARTED,
            data={"telegram_id": message.from_user.id if message.from_user else None},
        )
        await send_registration_screen(message, session)

    @router.message(F.contact)
    async def on_contact(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
        user: User | None,
        roles: set[str],
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

        reg_user, created = await users_svc.register_user(
            session,
            telegram_id=sender.id,
            phone=phone,
            username=sender.username,
            name=users_svc.display_name(sender.first_name, sender.last_name),
        )
        if not created and reg_user.anonymized_at is not None:
            # Returning after a completed personal-data deletion: a fresh registration.
            reg_user.anonymized_at = None
            reg_user.registered_at = datetime.now(UTC)
            await events.log_event(session, events.USER_REGISTERED, user_id=reg_user.id)
            created = True

        versions = await legal.get_active_versions(session)
        await legal.record_acceptance(session, reg_user.id, [v.id for v in versions.values()])
        await state.clear()

        if user is not None and not created:
            # Already registered user re-shared the contact: phone refreshed.
            await message.answer(t.MAIN_MENU, reply_markup=kb.main_menu(roles))
            return
        current_roles = await users_svc.get_roles(session, reg_user.id)
        await message.answer(t.REGISTRATION_DONE)
        await message.answer(t.MAIN_MENU, reply_markup=kb.main_menu(current_roles))

    return router
