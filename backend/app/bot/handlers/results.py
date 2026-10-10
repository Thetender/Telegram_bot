"""Search results, pagination and snapshot-bound actions (FR §5.1, §6; UI/UX §4.1, §5)."""

from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import texts as t
from app.bot.handlers.search import SCb, edit_or_send, render_grid
from app.config import Settings
from app.integrations.thetender import Auction, TenderClient, TenderError
from app.integrations.thetender.client import DEFAULT_PAGESIZE
from app.models import SearchSnapshot, User
from app.services import events
from app.services import search as search_svc
from app.services.links import tracked_url
from app.services.search_params import format_number, normalize, to_api

log = logging.getLogger(__name__)

# Telegram limit is 4096 characters of *visible* text (HTML tags and link
# addresses do not count), keep a small margin.
MESSAGE_BUDGET = 4000
_TAG = re.compile(r"<[^>]+>")


def visible_len(text: str) -> int:
    return len(html.unescape(_TAG.sub("", text)))


class RCb(CallbackData, prefix="r"):
    a: str  # more | retry | mon | edit | editok | new | newok
    s: int  # snapshot id
    p: int = 0


def format_auction(auction: Auction, link: str, position: int) -> str:
    price = f"{format_number(auction.price)} грн" if auction.price not in (None, "") else "—"
    return (
        f"<b>{position}. {html.escape(auction.name)}</b>\n"
        f"{t.RESULT_PRICE.format(price=price)}\n"
        f'<a href="{html.escape(link, quote=True)}">{t.RESULT_LINK}</a>'
    )


def split_messages(header: str, blocks: list[str]) -> list[str]:
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if visible_len(candidate) > MESSAGE_BUDGET and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def results_keyboard(snapshot: SearchSnapshot, next_page: int | None) -> InlineKeyboardMarkup:
    s = snapshot.id
    rows = []
    if next_page:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t.BTN_MORE, callback_data=RCb(a="more", s=s, p=next_page).pack()
                )
            ]
        )
    if snapshot.source == "SEARCH":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t.BTN_ENABLE_MONITORING, callback_data=RCb(a="mon", s=s).pack()
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t.BTN_CHANGE_PARAMS, callback_data=RCb(a="edit", s=s).pack()),
            InlineKeyboardButton(text=t.BTN_NEW_SEARCH, callback_data=RCb(a="new", s=s).pack()),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def error_keyboard(snapshot: SearchSnapshot, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t.BTN_RETRY, callback_data=RCb(a="retry", s=snapshot.id, p=page).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text=t.BTN_CHANGE_PARAMS, callback_data=RCb(a="edit", s=snapshot.id).pack()
                )
            ],
        ]
    )


async def send_results_page(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    settings: Settings,
    tender: TenderClient,
    user: User,
    snapshot: SearchSnapshot,
    page: int,
) -> str:
    """Fetch one API page for the snapshot and send it.

    Returns "sent", "duplicate" (page already sent by a parallel tap; nothing
    sent) or "error" (API failure; an error message with Retry was sent)."""
    try:
        result = await tender.search_auctions(to_api(snapshot.params), page=page)
    except TenderError as exc:
        log.warning("Search failed for snapshot %s page %s: %s", snapshot.id, page, exc.kind)
        await bot.send_message(chat_id, t.API_ERROR, reply_markup=error_keyboard(snapshot, page))
        return "error"

    if not await search_svc.mark_page_shown(session, snapshot.id, page):
        return "duplicate"
    if page == 1:
        await events.log_event(
            session,
            events.SEARCH_COMPLETED,
            user_id=user.id,
            data={"snapshot_id": snapshot.id, "items_count": result.items_count},
        )

    if not result.items:
        text = t.ZERO_RESULTS if page == 1 else t.PAGE_ALREADY_SHOWN
        if tender.is_mock:
            text = f"{t.DEMO_BANNER}\n\n{text}"
        await bot.send_message(chat_id, text, reply_markup=results_keyboard(snapshot, None))
        return "sent"

    header_parts = []
    if tender.is_mock:
        header_parts.append(t.DEMO_BANNER)
    if page == 1:
        header_parts.append(t.RESULTS_FOUND.format(count=result.items_count))
    # Continuous numbering across "Показати ще" pages: 1..N.
    offset = (page - 1) * DEFAULT_PAGESIZE
    shown = [a for a in result.items if a.url]
    blocks = [
        format_auction(
            a,
            tracked_url(
                settings.public_base_url,
                settings.link_signing_key,
                user.id,
                a.url,
                "SEARCH",
                a.number,
            ),
            offset + i + 1,
        )
        for i, a in enumerate(shown)
    ]
    messages = split_messages("\n".join(header_parts), blocks)
    next_page = page + 1 if result.has_more else None
    for i, text in enumerate(messages):
        markup = results_keyboard(snapshot, next_page) if i == len(messages) - 1 else None
        await bot.send_message(chat_id, text, reply_markup=markup)
    return "sent"


def create_router() -> Router:
    router = Router(name="results")
    router.callback_query.filter(filters.registered)

    async def load(callback: CallbackQuery, data: RCb, session: AsyncSession, user: User):
        snap = await search_svc.get_snapshot(session, data.s, user.id)
        if snap is None:
            await callback.answer(t.STALE_ACTION, show_alert=True)
        return snap

    @router.callback_query(RCb.filter(F.a.in_({"more", "retry"})))
    async def on_page(
        callback: CallbackQuery,
        callback_data: RCb,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
        settings: Settings,
    ) -> None:
        snap = await load(callback, callback_data, session, user)
        if snap is None:
            return
        if callback_data.p <= snap.pages_shown:
            await callback.answer(t.PAGE_ALREADY_SHOWN)
            return
        await callback.answer()
        status = await send_results_page(
            callback.bot,
            callback.from_user.id,
            session,
            settings,
            tender,
            user,
            snap,
            callback_data.p,
        )
        if status != "sent":
            return
        # Remove "Показати ще" from the message that was tapped.
        if callback_data.a == "more" and isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=results_keyboard(snap, None))
            except Exception:  # noqa: BLE001 - cosmetic only
                pass

    @router.callback_query(RCb.filter(F.a == "mon"))
    async def on_monitoring(callback: CallbackQuery) -> None:
        await callback.answer(t.MONITORING_SOON, show_alert=True)

    def confirm(yes_text: str, yes_action: str, snap: SearchSnapshot) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=yes_text, callback_data=RCb(a=yes_action, s=snap.id).pack()
                    )
                ],
                [InlineKeyboardButton(text=t.BTN_KEEP_CURRENT, callback_data=SCb(a="open").pack())],
            ]
        )

    @router.callback_query(RCb.filter(F.a.in_({"edit", "new"})))
    async def on_edit_or_new(
        callback: CallbackQuery,
        callback_data: RCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
    ) -> None:
        snap = await load(callback, callback_data, session, user)
        if snap is None:
            return
        await state.clear()
        draft = normalize(await search_svc.get_draft(session, user.id, lock=True))
        unsaved = bool(draft) and draft != normalize(snap.params)
        await callback.answer()
        if unsaved:
            if callback_data.a == "edit":
                markup = confirm(t.BTN_YES_REPLACE, "editok", snap)
                await callback.bot.send_message(
                    callback.from_user.id, t.CONFIRM_REPLACE_DRAFT, reply_markup=markup
                )
            else:
                markup = confirm(t.BTN_YES_NEW, "newok", snap)
                await callback.bot.send_message(
                    callback.from_user.id, t.CONFIRM_NEW_SEARCH, reply_markup=markup
                )
            return
        params = snap.params if callback_data.a == "edit" else {}
        await search_svc.save_draft(session, user.id, params)
        # Straight to the parameter grid: the user came here to change something.
        text, markup = render_grid(params)
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)

    @router.callback_query(RCb.filter(F.a.in_({"editok", "newok"})))
    async def on_confirmed(
        callback: CallbackQuery,
        callback_data: RCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
    ) -> None:
        snap = await load(callback, callback_data, session, user)
        if snap is None:
            return
        await state.clear()
        params = snap.params if callback_data.a == "editok" else {}
        await search_svc.save_draft(session, user.id, params)
        await edit_or_send(callback, *render_grid(params))
        await callback.answer()

    return router
