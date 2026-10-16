"""Asking for the phone number only when it is needed: the first monitoring
and a consultation request. The pending action resumes after the contact."""

from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext

from app.bot import keyboards as kb
from app.bot import texts as t
from app.models import User

AFTER_MONITORING = "mon"
AFTER_CONSULTATION = "consult"


async def ask_phone(
    bot: Bot, state: FSMContext, user: User, after: str, snapshot_id: int | None = None
) -> None:
    await state.clear()
    await state.set_data({"after_phone": after, "snap": snapshot_id})
    text = t.PHONE_FOR_MONITORING if after == AFTER_MONITORING else t.PHONE_FOR_CONSULTATION
    back = t.BTN_BACK_TO_SEARCH_PHONE if after == AFTER_MONITORING else t.BTN_CANCEL_PHONE
    await bot.send_message(user.telegram_id, text, reply_markup=kb.share_phone(back))
