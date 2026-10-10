"""Admin invitation accept/decline inside the bot (FR §11.1)."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import keyboards as kb
from app.bot import texts as t
from app.models import User
from app.services import admin as admin_svc
from app.services import users as users_svc


class InvCb(CallbackData, prefix="inv"):
    a: str  # y | n
    i: int


def invitation_text(inviter: User) -> str:
    return (
        "🔐 <b>Запрошення стати адміністратором The Tender</b>\n\n"
        f"{html.escape(inviter.name or 'Адміністратор')} запрошує вас керувати "
        "Telegram-ботом через веб-адмінку.\n\nПрийняти запрошення?"
    )


def create_router() -> Router:
    router = Router(name="admin_invite")
    router.callback_query.filter(filters.registered)

    @router.callback_query(InvCb.filter(F.a.in_({"y", "n"})))
    async def on_answer(
        callback: CallbackQuery, callback_data: InvCb, session: AsyncSession, user: User
    ) -> None:
        result = await admin_svc.respond_invitation(
            session, callback_data.i, user, accept=callback_data.a == "y"
        )
        if result == "stale":
            await callback.answer("Запрошення вже неактуальне.", show_alert=True)
            return
        if result == "accepted":
            text = (
                "✅ Ви адміністратор. Вхід в адмінку — кнопкою «Увійти через Telegram» "
                "на сторінці /admin сервісу."
            )
        else:
            text = "Запрошення відхилено."
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text)
        roles = await users_svc.get_roles(session, user.id)
        await callback.bot.send_message(
            callback.from_user.id, t.MAIN_MENU, reply_markup=kb.main_menu(roles)
        )

    return router
