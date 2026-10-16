"""Help → «💬 Відгуки та пропозиції»: topic → one message (text / screenshot) →
thanks; admins are notified; replies from the Dashboard can be answered."""

from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import keyboards as kb
from app.bot import texts as t
from app.config import Settings
from app.models import Feedback, User
from app.services import feedback as svc

log = logging.getLogger(__name__)


class FbCb(CallbackData, prefix="fb"):
    a: str  # topic | reply
    v: str = ""


class FeedbackInput(StatesGroup):
    text = State()


def topics_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=FbCb(a="topic", v=key).pack())]
        for key, label in svc.TOPICS.items()
    ]
    rows.append(
        [InlineKeyboardButton(text=t.BTN_BACK, callback_data=kb.HelpCb(section="menu").pack())]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reply_keyboard(feedback_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t.BTN_FB_REPLY, callback_data=FbCb(a="reply", v=str(feedback_id)).pack()
                )
            ]
        ]
    )


def leave_feedback_button() -> InlineKeyboardButton:
    """For campaign messages: opens the topic list."""
    return InlineKeyboardButton(
        text=t.BTN_LEAVE_FEEDBACK, callback_data=kb.HelpCb(section="fb").pack()
    )


async def show_topics(callback: CallbackQuery) -> None:
    from app.bot.handlers.help import show

    await show(callback, t.FEEDBACK_TITLE, topics_keyboard())


async def notify_admins(
    bot: Bot, session: AsyncSession, settings: Settings, fb: Feedback, user: User, text: str,
    followup: bool = False,
) -> None:  # fmt: skip
    from app.services.consultations import notification_targets

    _, admins = await notification_targets(session)
    link = f"{settings.public_base_url or ''}/admin/feedback/{fb.id}"
    short = html.escape(text[:600] + ("…" if len(text) > 600 else "")) or "📷 скриншот"
    if followup:
        message = t.FEEDBACK_ADMIN_FOLLOWUP.format(
            id=fb.id, name=html.escape(user.name or "—"), text=short, link=link
        )
    else:
        message = t.FEEDBACK_ADMIN_NOTICE.format(
            id=fb.id,
            topic=svc.TOPICS.get(fb.topic, fb.topic),
            name=html.escape(user.name or "—"),
            username=f" (@{html.escape(user.username)})" if user.username else "",
            text=short,
            link=link,
        )
    for admin in admins:
        try:
            await bot.send_message(admin.telegram_id, message)
        except TelegramAPIError as exc:
            log.info("Feedback %s: admin %s not notified: %s", fb.id, admin.id, exc)


def create_router() -> Router:
    router = Router(name="feedback")
    router.message.filter(filters.registered)
    router.callback_query.filter(filters.registered)

    @router.callback_query(FbCb.filter(F.a == "topic"))
    async def on_topic(callback: CallbackQuery, callback_data: FbCb, state: FSMContext) -> None:
        if callback_data.v not in svc.TOPICS:
            await callback.answer(t.STALE_ACTION)
            return
        await state.set_state(FeedbackInput.text)
        await state.set_data({"topic": callback_data.v})
        from app.bot.handlers.help import show

        back = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t.BTN_BACK_TO_TOPICS, callback_data=kb.HelpCb(section="fb").pack()
                    )
                ]
            ]
        )
        await show(callback, t.FEEDBACK_PROMPT.format(topic=svc.TOPICS[callback_data.v]), back)
        await callback.answer()

    @router.callback_query(FbCb.filter(F.a == "reply"))
    async def on_reply(
        callback: CallbackQuery,
        callback_data: FbCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
    ) -> None:
        fb = (
            await session.get(Feedback, int(callback_data.v)) if callback_data.v.isdigit() else None
        )
        if fb is None or fb.user_id != user.id:
            await callback.answer(t.STALE_ACTION)
            return
        await state.set_state(FeedbackInput.text)
        await state.set_data({"feedback_id": fb.id})
        await callback.answer()
        await callback.bot.send_message(user.telegram_id, t.FEEDBACK_REPLY_PROMPT)

    @router.message(FeedbackInput.text)
    async def on_text(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        settings: Settings,
        roles: set[str],
    ) -> None:
        text = (message.text or message.caption or "").strip()
        photo = message.photo[-1].file_id if message.photo else None
        if not text and not photo:
            await message.answer(t.FEEDBACK_EMPTY)
            return
        data = await state.get_data()
        await state.clear()
        bot = message.bot
        assert bot is not None
        if data.get("feedback_id"):
            fb = await session.get(Feedback, int(data["feedback_id"]))
            if fb is None or fb.user_id != user.id:
                await message.answer(t.STALE_ACTION)
                return
            await svc.add_message(session, fb, "IN", text, photo, user.id)
            await session.commit()
            await message.answer(t.FEEDBACK_THANKS, reply_markup=kb.main_menu(roles))
            await notify_admins(bot, session, settings, fb, user, text, followup=True)
            return
        fb = await svc.create(session, user, data.get("topic", "other"), text, photo)
        await session.commit()
        await message.answer(t.FEEDBACK_THANKS, reply_markup=kb.main_menu(roles))
        await notify_admins(bot, session, settings, fb, user, text)

    return router
