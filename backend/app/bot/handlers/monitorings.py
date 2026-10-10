"""Monitorings: create from a search snapshot, «Мої моніторинги» list,
detail, results, edit (separate draft, explicit save), rename, delete
(FR §7, UI/UX §6). The Tender is canonical: every change goes to the API
first and nothing is reported as saved when the API call failed."""

from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import texts as t
from app.bot.callbacks import MCb
from app.bot.handlers.results import RCb, send_results_page
from app.bot.handlers.search import SCb, edit_or_send, render_edit_screen
from app.config import Settings
from app.integrations.thetender import TenderClient, TenderError
from app.models import Monitoring, User
from app.services import monitorings as svc
from app.services import search as search_svc
from app.services.search_params import is_effective, normalize, summary_lines

log = logging.getLogger(__name__)

PER_PAGE = 10


class MonitoringInput(StatesGroup):
    name = State()  # data: {"mode": "create", "snap": id} | {"mode": "rename"}


def _b(text: str, **cb) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MCb(**cb).pack())


def list_screen(rows: list[Monitoring], page: int) -> tuple[str, InlineKeyboardMarkup]:
    search_btn = InlineKeyboardButton(text=t.BTN_CREATE_SEARCH, callback_data=SCb(a="open").pack())
    if not rows:
        return f"{t.MON_TITLE}\n\n{t.MON_EMPTY}", InlineKeyboardMarkup(
            inline_keyboard=[[search_btn]]
        )
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * PER_PAGE : (page + 1) * PER_PAGE]
    kb = [[_b(f"🔔 {m.name}", a="show", i=m.id, p=page)] for m in chunk]
    nav = []
    if page > 0:
        nav.append(_b("⬅️", a="list", p=page - 1))
    if pages > 1:
        nav.append(_b(f"{page + 1}/{pages}", a="list", p=page))
    if page < pages - 1:
        nav.append(_b("➡️", a="list", p=page + 1))
    if nav:
        kb.append(nav)
    kb.append([search_btn])
    text = f"{t.MON_TITLE} ({len(rows)})\n\n{t.MON_LIST_HINT}"
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


def detail_screen(row: Monitoring, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    lines = [t.MON_DETAIL.format(name=html.escape(row.name)), ""]
    lines += [f"• {label}: {html.escape(value)}" for label, value in summary_lines(row.params)]
    kb = [
        [_b(t.BTN_SHOW_RESULTS, a="res", i=row.id)],
        [_b(t.BTN_EDIT, a="edit", i=row.id), _b(t.BTN_DELETE, a="del", i=row.id, p=page)],
        [_b(t.BTN_TO_LIST, a="list", p=page)],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


async def send_list(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    tender: TenderClient,
    user: User,
    page: int = 0,
    callback: CallbackQuery | None = None,
    prefix: str = "",
) -> None:
    """The canonical list comes from The Tender; the local index is refreshed."""
    try:
        rows = await svc.sync_user(session, tender, user)
    except TenderError as exc:
        log.warning("Monitoring list for user %s failed: %s", user.id, exc.kind)
        retry = InlineKeyboardMarkup(inline_keyboard=[[_b(t.BTN_RETRY, a="list", p=page)]])
        if callback is not None:
            await edit_or_send(callback, t.MON_API_ERROR, retry)
        else:
            await bot.send_message(chat_id, t.MON_API_ERROR, reply_markup=retry)
        return
    text, markup = list_screen(rows, page)
    text = f"{prefix}{text}"
    if callback is not None:
        await edit_or_send(callback, text, markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)


def _errors_text(exc: TenderError) -> str:
    items = exc.errors or [str(exc)]
    return "\n".join(f"• {html.escape(e)}" for e in items)


def create_router() -> Router:
    router = Router(name="monitorings")
    router.message.filter(filters.registered)
    router.callback_query.filter(filters.registered)

    async def stale(callback: CallbackQuery) -> None:
        await callback.answer(t.STALE_ACTION, show_alert=True)

    async def owned(
        callback: CallbackQuery,
        session: AsyncSession,
        tender: TenderClient,
        user: User,
        monitoring_id: int,
    ) -> Monitoring | None:
        row = await svc.get_owned(session, monitoring_id, user.id)
        if row is None:
            await callback.answer()
            await send_list(
                callback.bot,
                user.telegram_id,
                session,
                tender,
                user,
                callback=callback,
                prefix=f"{t.MON_NOT_FOUND}\n\n",
            )
        return row

    # ---------- create from a search snapshot ----------

    @router.callback_query(RCb.filter(F.a == "mon"))
    async def on_enable(
        callback: CallbackQuery,
        callback_data: RCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
    ) -> None:
        snap = await search_svc.get_snapshot(session, callback_data.s, user.id)
        if snap is None or snap.source != "SEARCH" or not is_effective(snap.params):
            await stale(callback)
            return
        await state.set_state(MonitoringInput.name)
        await state.set_data({"mode": "create", "snap": snap.id})
        suggestion = svc.suggested_name(snap.params)
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [_b(t.BTN_USE_NAME.format(name=suggestion), a="usename", i=snap.id)],
                [_b(t.BTN_CANCEL, a="noname")],
            ]
        )
        await callback.answer()
        await callback.bot.send_message(user.telegram_id, t.MON_ASK_NAME, reply_markup=markup)

    async def create_from_snapshot(
        bot: Bot,
        session: AsyncSession,
        tender: TenderClient,
        state: FSMContext,
        user: User,
        snapshot_id: int,
        name: str,
    ) -> None:
        snap = await search_svc.get_snapshot(session, snapshot_id, user.id)
        if snap is None:
            await state.clear()
            await bot.send_message(user.telegram_id, t.STALE_ACTION)
            return
        try:
            row = await svc.create(session, tender, user, name, snap.params)
        except TenderError as exc:
            log.warning("Monitoring create for user %s failed: %s", user.id, exc.kind)
            if exc.kind == "validation":
                await state.clear()
                await bot.send_message(
                    user.telegram_id, t.MON_SAVE_REJECTED.format(errors=_errors_text(exc))
                )
            else:
                # Keep the state: the user can simply send the name again.
                await bot.send_message(user.telegram_id, t.MON_SAVE_RETRY)
            return
        await state.clear()
        markup = InlineKeyboardMarkup(inline_keyboard=[[_b(t.BTN_MY_MONITORINGS, a="list")]])
        await bot.send_message(
            user.telegram_id, t.MON_CREATED.format(name=html.escape(row.name)), reply_markup=markup
        )

    @router.callback_query(MCb.filter(F.a == "usename"))
    async def on_use_suggested(
        callback: CallbackQuery,
        callback_data: MCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        snap = await search_svc.get_snapshot(session, callback_data.i, user.id)
        if snap is None:
            await stale(callback)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        await create_from_snapshot(
            callback.bot,
            session,
            tender,
            state,
            user,
            snap.id,
            svc.suggested_name(snap.params),
        )

    @router.callback_query(MCb.filter(F.a == "noname"))
    async def on_cancel_name(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        data = await state.get_data()
        await state.clear()
        await callback.answer()
        if data.get("mode") == "rename":
            draft = await svc.get_edit(session, user.id)
            if draft is not None:
                await edit_or_send(callback, *render_edit_screen(draft.name, draft.params))
                return
        await edit_or_send(callback, t.MON_CREATE_CANCELLED, None)

    @router.message(MonitoringInput.name, F.text)
    async def on_name(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        name = svc.clean_name(message.text)
        if name is None:
            await message.answer(t.MON_BAD_NAME)
            return
        data = await state.get_data()
        if data.get("mode") == "rename":
            await state.clear()
            if await svc.get_edit(session, user.id) is None:
                await message.answer(t.STALE_ACTION)
                return
            await svc.rename_edit(session, user.id, name)
            draft = await svc.get_edit(session, user.id)
            assert draft is not None
            text, markup = render_edit_screen(draft.name, draft.params)
            await message.answer(text, reply_markup=markup)
            return
        await create_from_snapshot(
            message.bot, session, tender, state, user, int(data.get("snap") or 0), name
        )

    # ---------- list / detail / results ----------

    @router.callback_query(MCb.filter(F.a == "list"))
    async def on_list(
        callback: CallbackQuery,
        callback_data: MCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        await state.clear()
        await callback.answer()
        await send_list(
            callback.bot, user.telegram_id, session, tender, user, callback_data.p, callback
        )

    @router.callback_query(MCb.filter(F.a == "show"))
    async def on_show(
        callback: CallbackQuery,
        callback_data: MCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        await state.clear()
        row = await owned(callback, session, tender, user, callback_data.i)
        if row is None:
            return
        await edit_or_send(callback, *detail_screen(row, callback_data.p))
        await callback.answer()

    @router.callback_query(MCb.filter(F.a == "res"))
    async def on_results(
        callback: CallbackQuery,
        callback_data: MCb,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
        settings: Settings,
    ) -> None:
        row = await owned(callback, session, tender, user, callback_data.i)
        if row is None:
            return
        await callback.answer()
        snapshot = await search_svc.create_snapshot(
            session, user.id, normalize(row.params), source="MONITORING"
        )
        await send_results_page(
            callback.bot, user.telegram_id, session, settings, tender, user, snapshot, 1
        )

    # ---------- edit ----------

    @router.callback_query(MCb.filter(F.a == "edit"))
    async def on_edit(
        callback: CallbackQuery,
        callback_data: MCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        await state.clear()
        row = await owned(callback, session, tender, user, callback_data.i)
        if row is None:
            return
        draft = await svc.start_edit(session, user.id, row)
        await edit_or_send(callback, *render_edit_screen(draft.name, draft.params))
        await callback.answer()

    @router.callback_query(MCb.filter(F.a == "rename"))
    async def on_rename(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        draft = await svc.get_edit(session, user.id)
        if draft is None:
            await stale(callback)
            return
        await state.set_state(MonitoringInput.name)
        await state.set_data({"mode": "rename"})
        markup = InlineKeyboardMarkup(inline_keyboard=[[_b(t.BTN_CANCEL, a="noname")]])
        await edit_or_send(callback, t.MON_ASK_RENAME.format(name=html.escape(draft.name)), markup)
        await callback.answer()

    @router.callback_query(MCb.filter(F.a == "cancel"))
    async def on_cancel_edit(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        await state.clear()
        draft = await svc.get_edit(session, user.id)
        if draft is None:
            await stale(callback)
            return
        monitoring_id = draft.monitoring_id
        await svc.clear_edit(session, user.id)
        row = await owned(callback, session, tender, user, monitoring_id)
        if row is None:
            return
        await edit_or_send(callback, *detail_screen(row))
        await callback.answer()

    @router.callback_query(MCb.filter(F.a == "save"))
    async def on_save(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        await state.clear()
        draft = await svc.get_edit(session, user.id, lock=True)
        if draft is None:
            await stale(callback)
            return
        if not is_effective(draft.params):
            await callback.answer(t.SEARCH_NEED_PARAM, show_alert=True)
            return
        row = await svc.get_owned(session, draft.monitoring_id, user.id)
        if row is None:
            await svc.clear_edit(session, user.id)
            await callback.answer()
            await send_list(
                callback.bot,
                user.telegram_id,
                session,
                tender,
                user,
                callback=callback,
                prefix=f"{t.MON_NOT_FOUND}\n\n",
            )
            return
        try:
            row = await svc.update(session, tender, user, row, draft.name, draft.params)
        except TenderError as exc:
            log.warning("Monitoring update for user %s failed: %s", user.id, exc.kind)
            if exc.kind == "validation":
                text = t.MON_SAVE_REJECTED.format(errors=_errors_text(exc))
            else:
                text = t.MON_API_ERROR
            # The edit draft is kept: nothing is lost, the user can retry.
            await callback.answer()
            await callback.bot.send_message(user.telegram_id, text)
            return
        await svc.clear_edit(session, user.id)
        text, markup = detail_screen(row)
        await edit_or_send(callback, f"{t.MON_SAVED}\n\n{text}", markup)
        await callback.answer()

    # ---------- delete ----------

    @router.callback_query(MCb.filter(F.a == "del"))
    async def on_delete(
        callback: CallbackQuery,
        callback_data: MCb,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        row = await owned(callback, session, tender, user, callback_data.i)
        if row is None:
            return
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _b(t.BTN_YES_DELETE, a="delok", i=row.id, p=callback_data.p),
                    _b(t.BTN_NO, a="show", i=row.id, p=callback_data.p),
                ]
            ]
        )
        await edit_or_send(
            callback, t.MON_CONFIRM_DELETE.format(name=html.escape(row.name)), markup
        )
        await callback.answer()

    @router.callback_query(MCb.filter(F.a == "delok"))
    async def on_delete_ok(
        callback: CallbackQuery,
        callback_data: MCb,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        row = await owned(callback, session, tender, user, callback_data.i)
        if row is None:
            return
        name = row.name
        try:
            await svc.delete(session, tender, user, row)
        except TenderError as exc:
            log.warning("Monitoring delete for user %s failed: %s", user.id, exc.kind)
            await callback.answer(t.MON_API_ERROR, show_alert=True)
            return
        await callback.answer()
        await send_list(
            callback.bot,
            user.telegram_id,
            session,
            tender,
            user,
            callback_data.p,
            callback,
            prefix=f"{t.MON_DELETED.format(name=html.escape(name))}\n\n",
        )

    return router
