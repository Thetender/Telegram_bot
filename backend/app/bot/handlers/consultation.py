"""Consultation: client request and manager workflow (FR §9, §10; UI/UX §7, §8)."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import texts as t
from app.config import Settings
from app.models import ConsultationRequest, User
from app.services import consultations as svc

log = logging.getLogger(__name__)


class CCb(CallbackData, prefix="c"):
    a: str  # req | claim | res | view | list
    r: int = 0  # request id
    v: str = ""  # result code / list kind


def _btn(text: str, a: str, r: int = 0, v: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CCb(a=a, r=r, v=v).pack())


def fmt_local(dt: datetime | None, tz: str) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


def request_card(req: ConsultationRequest, tz: str) -> str:
    username = f"@{req.contact_username}" if req.contact_username else "—"
    return t.REQ_CARD.format(
        id=req.id,
        name=html.escape(req.contact_name or "—"),
        phone=html.escape(req.contact_phone or "—"),
        username=html.escape(username),
        created=fmt_local(req.created_at, tz),
    )


def result_keyboard(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(t.BTN_INTERESTED, "res", req_id, "INTERESTED"),
                _btn(t.BTN_DECLINED, "res", req_id, "DECLINED"),
            ]
        ]
    )


async def _safe_edit(
    bot: Bot, chat_id: int, message_id: int, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup
        )
    except TelegramAPIError as exc:
        # Failure to update another manager's old message must not roll back anything.
        log.info("Could not edit consultation message %s/%s: %s", chat_id, message_id, exc)


async def notify_staff(
    bot: Bot, session: AsyncSession, settings: Settings, req: ConsultationRequest
) -> None:
    """Send the new request to all enabled managers; fall back to admins."""
    managers, admins = await svc.notification_targets(session)
    card = request_card(req, settings.timezone)
    delivered = 0
    for manager in managers:
        try:
            msg = await bot.send_message(
                manager.telegram_id,
                f"{t.REQ_NEW_HEADER}\n\n{card}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[_btn(t.BTN_CLAIM, "claim", req.id)]]
                ),
            )
            await svc.record_notification(session, req.id, manager, "MANAGER", msg.message_id)
            delivered += 1
        except TelegramAPIError as exc:
            log.warning("Consultation %s: manager %s not notified: %s", req.id, manager.id, exc)
            await svc.record_notification(session, req.id, manager, "MANAGER", None, str(exc))
    if delivered:
        return
    for admin in admins:
        try:
            msg = await bot.send_message(admin.telegram_id, f"{t.ADMIN_FALLBACK_HEADER}\n\n{card}")
            await svc.record_notification(session, req.id, admin, "ADMIN_FALLBACK", msg.message_id)
        except TelegramAPIError as exc:
            log.warning("Consultation %s: admin %s not notified: %s", req.id, admin.id, exc)
            await svc.record_notification(session, req.id, admin, "ADMIN_FALLBACK", None, str(exc))


async def show_consultation(
    bot: Bot, chat_id: int, session: AsyncSession, settings: Settings, user: User
) -> None:
    existing = await svc.get_open_request(session, user.id)
    if existing is not None:
        await bot.send_message(
            chat_id, t.CONSULT_ALREADY.format(company_phone=settings.consultation_phone)
        )
        return
    text = f"{t.CONSULT_TITLE}\n\n" + t.CONSULT_INTRO.format(phone=html.escape(user.phone or "—"))
    await bot.send_message(
        chat_id,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn(t.BTN_ORDER_CONSULTATION, "req")]]
        ),
    )


async def show_my_requests(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    settings: Settings,
    user: User,
    active: bool,
    edit: CallbackQuery | None = None,
) -> None:
    items = await svc.manager_requests(session, user.id, active=active)
    lines = [t.MY_REQUESTS_TITLE, "", f"<b>{t.BTN_ACTIVE if active else t.BTN_DONE}</b>"]
    rows = [
        [
            _btn(("• " if active else "") + t.BTN_ACTIVE, "list", v="a"),
            _btn(("" if active else "• ") + t.BTN_DONE, "list", v="d"),
        ]
    ]
    if not items:
        lines.append(t.MY_REQUESTS_EMPTY_ACTIVE if active else t.MY_REQUESTS_EMPTY_DONE)
    for req in items:
        when = fmt_local(req.claimed_at if active else req.completed_at, settings.timezone)
        suffix = (
            ""
            if active
            else " "
            + (t.RESULT_LABELS.get(req.result or "", "⛔️") if req.status == "COMPLETED" else "⛔️")
        )
        label = f"№{req.id} · {req.contact_name or '—'} · {when}{suffix}"
        rows.append([_btn(label[:60], "view", req.id)])
    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit is not None and isinstance(edit.message, Message):
        try:
            await edit.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
    await bot.send_message(chat_id, text, reply_markup=markup)


def status_tail(
    req: ConsultationRequest, manager_name: str | None, viewer: User
) -> tuple[str, InlineKeyboardMarkup | None]:
    if req.status == "IN_PROGRESS" and req.claimed_by == viewer.id:
        return t.REQ_IN_PROGRESS_MINE, result_keyboard(req.id)
    if req.status == "IN_PROGRESS":
        return t.REQ_CLAIMED_BY.format(manager=html.escape(manager_name or "—")), None
    if req.status == "COMPLETED":
        return t.REQ_COMPLETED.format(result=t.RESULT_LABELS.get(req.result or "", "—")), None
    if req.status == "CLOSED_BY_ADMIN":
        return t.REQ_CLOSED_BY_ADMIN, None
    return "", InlineKeyboardMarkup(inline_keyboard=[[_btn(t.BTN_CLAIM, "claim", req.id)]])


def create_router() -> Router:
    router = Router(name="consultation")
    router.message.filter(filters.registered)
    router.callback_query.filter(filters.registered)

    @router.message(F.text == t.BTN_CONSULTATION)
    async def on_menu_consultation(
        message: Message, state: FSMContext, session: AsyncSession, settings: Settings, user: User
    ) -> None:
        await state.clear()
        await show_consultation(message.bot, message.chat.id, session, settings, user)

    @router.message(F.text == t.BTN_MY_REQUESTS, filters.is_manager)
    async def on_my_requests(
        message: Message, state: FSMContext, session: AsyncSession, settings: Settings, user: User
    ) -> None:
        await state.clear()
        await show_my_requests(message.bot, message.chat.id, session, settings, user, active=True)

    @router.callback_query(CCb.filter(F.a == "open"))
    async def on_open(
        callback: CallbackQuery, session: AsyncSession, settings: Settings, user: User
    ) -> None:
        await callback.answer()
        await show_consultation(callback.bot, callback.from_user.id, session, settings, user)

    @router.callback_query(CCb.filter(F.a == "req"))
    async def on_request(
        callback: CallbackQuery, session: AsyncSession, settings: Settings, user: User
    ) -> None:
        req, created = await svc.create_request(session, user)
        phone = settings.consultation_phone
        if not created:
            await callback.answer()
            await callback.bot.send_message(
                callback.from_user.id, t.CONSULT_ALREADY.format(company_phone=phone)
            )
            return
        # Commit first so a fast manager can claim the request immediately.
        await session.commit()
        weekend = svc.is_weekend(datetime.now(UTC), settings.timezone)
        text = (t.CONSULT_WEEKEND if weekend else t.CONSULT_WORKDAY).format(company_phone=phone)
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        await callback.answer()
        await callback.bot.send_message(callback.from_user.id, text)
        await notify_staff(callback.bot, session, settings, req)

    @router.callback_query(CCb.filter(F.a == "claim"), filters.is_manager)
    async def on_claim(
        callback: CallbackQuery,
        callback_data: CCb,
        session: AsyncSession,
        settings: Settings,
        user: User,
    ) -> None:
        req_id = callback_data.r
        won = await svc.claim(session, req_id, user)
        req = await session.get(ConsultationRequest, req_id)
        if req is None:
            await callback.answer(t.STALE_ACTION, show_alert=True)
            return
        await session.refresh(req)
        if not won:
            if req.claimed_by == user.id and req.status == "IN_PROGRESS":
                await callback.answer()
            else:
                claimer = await session.get(User, req.claimed_by) if req.claimed_by else None
                name = claimer.name if claimer else "інший менеджер"
                if req.status == "CLOSED_BY_ADMIN":
                    await callback.answer(
                        t.REQ_CLOSED_BY_ADMIN.replace("<b>", "").replace("</b>", ""),
                        show_alert=True,
                    )
                else:
                    await callback.answer(t.REQ_CLAIMED_ALERT.format(manager=name), show_alert=True)
            tail, markup = status_tail(req, None, user)
            if isinstance(callback.message, Message):
                await _safe_edit(
                    callback.bot,
                    callback.message.chat.id,
                    callback.message.message_id,
                    f"{request_card(req, settings.timezone)}\n\n{tail}",
                    markup,
                )
            return
        await session.commit()
        await callback.answer()
        card = request_card(req, settings.timezone)
        # Update every manager message about this request.
        for note in await svc.notifications_for(session, req_id):
            if note.message_id is None:
                continue
            if note.recipient_user_id == user.id:
                await _safe_edit(
                    callback.bot,
                    note.chat_id,
                    note.message_id,
                    f"{card}\n\n{t.REQ_IN_PROGRESS_MINE}",
                    result_keyboard(req_id),
                )
            else:
                await _safe_edit(
                    callback.bot,
                    note.chat_id,
                    note.message_id,
                    f"{card}\n\n{t.REQ_CLAIMED_BY.format(manager=html.escape(user.name or '—'))}",
                    None,
                )

    @router.callback_query(CCb.filter(F.a == "res"), filters.is_manager)
    async def on_result(
        callback: CallbackQuery,
        callback_data: CCb,
        session: AsyncSession,
        settings: Settings,
        user: User,
    ) -> None:
        if callback_data.v not in svc.RESULTS:
            await callback.answer(t.STALE_ACTION)
            return
        done = await svc.complete(session, callback_data.r, user, callback_data.v)
        req = await session.get(ConsultationRequest, callback_data.r)
        if req is None:
            await callback.answer(t.STALE_ACTION, show_alert=True)
            return
        await session.refresh(req)
        if not done and not (req.status == "COMPLETED" and req.claimed_by == user.id):
            await callback.answer(t.REQ_NOT_YOURS, show_alert=True)
        else:
            await callback.answer()
        tail, markup = status_tail(req, None, user)
        if isinstance(callback.message, Message):
            await _safe_edit(
                callback.bot,
                callback.message.chat.id,
                callback.message.message_id,
                f"{request_card(req, settings.timezone)}\n\n{tail}",
                markup,
            )

    @router.callback_query(CCb.filter(F.a == "list"), filters.is_manager)
    async def on_list(
        callback: CallbackQuery,
        callback_data: CCb,
        session: AsyncSession,
        settings: Settings,
        user: User,
    ) -> None:
        await callback.answer()
        await show_my_requests(
            callback.bot,
            callback.from_user.id,
            session,
            settings,
            user,
            active=callback_data.v != "d",
            edit=callback,
        )

    @router.callback_query(CCb.filter(F.a == "view"), filters.is_manager)
    async def on_view(
        callback: CallbackQuery,
        callback_data: CCb,
        session: AsyncSession,
        settings: Settings,
        user: User,
    ) -> None:
        req = await session.get(ConsultationRequest, callback_data.r)
        if req is None or req.claimed_by != user.id:
            await callback.answer(t.STALE_ACTION, show_alert=True)
            return
        await callback.answer()
        tail, markup = status_tail(req, None, user)
        await callback.bot.send_message(
            callback.from_user.id,
            f"{request_card(req, settings.timezone)}\n\n{tail}",
            reply_markup=markup,
        )

    return router
