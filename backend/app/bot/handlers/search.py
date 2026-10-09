"""Auction search parameter screen (FR §5, UI/UX §4).

One parameter screen; parameters can be set in any order; the draft is
persisted (search_drafts) and resumed. Text inputs use FSM states; menu
buttons and commands are handled earlier and always cancel pending input.
"""

from __future__ import annotations

import hashlib
import html
import logging
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import keyboards as kb
from app.bot import texts as t
from app.config import Settings
from app.integrations.thetender import TenderClient, TenderError
from app.models import User
from app.services import events
from app.services import search as search_svc
from app.services.search_params import (
    AREA_UNITS,
    AUCTION_TYPES,
    MAX_TEXT_LEN,
    PRICE_TYPES,
    REGIONS,
    format_number,
    is_effective,
    normalize,
    parse_number,
    summary_lines,
)

log = logging.getLogger(__name__)

CATEGORIES_PER_PAGE = 8
TEXT_PROMPTS = {
    "city": t.PROMPT_CITY,
    "keywords": t.PROMPT_KEYWORDS,
    "customer_name": t.PROMPT_ORGANIZER,
}
SINGLE_CHOICE = {"auction_type": AUCTION_TYPES, "start_price_type": PRICE_TYPES}
CLEARABLE = {"auction_type", "start_price_type", "category", "regions"}


class SCb(CallbackData, prefix="s"):
    a: str
    v: str = ""


class SearchInput(StatesGroup):
    text = State()
    min_value = State()
    max_value = State()


def _btn(text: str, a: str, v: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=SCb(a=a, v=v).pack())


def category_key(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()[:8]


# ---------- rendering ----------


def render_screen(params: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Search "home" screen.

    With chosen parameters: a short summary with the values as text and four
    buttons (Search / Add-change / Clear / Main menu), so the text stays on
    screen. With nothing chosen yet: straight to the parameter grid."""
    p = normalize(params)
    summary = summary_lines(p)
    if not summary:
        return render_grid(p)
    lines = [t.SEARCH_TITLE, "", "<b>Обрані параметри:</b>"]
    lines += [f"• {label}: {html.escape(value)}" for label, value in summary]
    rows = [
        [_btn(t.BTN_RUN_SEARCH, "run")],
        [_btn(t.BTN_EDIT_PARAMS, "grid")],
        [_btn(t.BTN_CLEAR_PARAMS, "clear"), _btn(t.BTN_MAIN_MENU, "menu")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def render_grid(params: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Parameter grid: ✅ marks parameters that are already chosen."""
    p = normalize(params)

    def mark(label: str, is_set: bool) -> str:
        return f"✅ {label}" if is_set else label

    rows = [
        [
            _btn(mark(t.BTN_P_DEAL, "auction_type" in p), "deal"),
            _btn(mark(t.BTN_P_TYPE, "start_price_type" in p), "type"),
        ],
        [
            _btn(mark(t.BTN_P_CATEGORY, "category" in p), "cat", "0"),
            _btn(mark(t.BTN_P_REGION, "regions" in p), "reg"),
        ],
        [
            _btn(mark(t.BTN_P_CITY, "city" in p), "txt", "city"),
            _btn(mark(t.BTN_P_KEYWORDS, "keywords" in p), "txt", "keywords"),
        ],
        [
            _btn(mark(t.BTN_P_ORGANIZER, "customer_name" in p), "txt", "customer_name"),
            _btn(mark(t.BTN_P_PRICE, "min_price" in p or "max_price" in p), "price"),
        ],
        [_btn(mark(t.BTN_P_AREA, "area_unit" in p), "area")],
    ]
    if summary_lines(p):
        text = f"{t.SEARCH_TITLE}\n\n{t.CHOOSE_PARAM}"
        rows.append([_btn(t.BTN_BACK, "open")])
    else:
        text = f"{t.SEARCH_TITLE}\n\n{t.SEARCH_HINT}"
        rows.append([_btn(t.BTN_MAIN_MENU, "menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def back_row(clear_target: str | None = None) -> list[InlineKeyboardButton]:
    """Bottom row of every parameter screen: [🗑 Очистити] (only when the
    parameter is set) on the left, [◀️ Назад] (to the grid) on the right."""
    row = [_btn(t.BTN_CLEAR_FIELD, "fclr", clear_target)] if clear_target else []
    row.append(_btn(t.BTN_BACK, "grid"))
    return row


def input_keyboard(skip: bool = False, clear_target: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if skip:
        rows.append([_btn(t.BTN_SKIP, "skip")])
    bottom = [_btn(t.BTN_CLEAR_FIELD, "fclr", clear_target)] if clear_target else []
    bottom.append(_btn(t.BTN_CANCEL, "cancel"))
    rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def regions_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    chosen = set(selected)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(REGIONS):
        label = f"✅ {name}" if name in chosen else name
        row.append(_btn(label, "regt", str(i)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    bottom = [_btn(t.BTN_CLEAR_FIELD, "fclr", "regions")] if chosen else []
    bottom.append(_btn(t.BTN_DONE, "open"))
    rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def categories_keyboard(names: list[str], page: int, current: str | None) -> InlineKeyboardMarkup:
    pages = max(1, -(-len(names) // CATEGORIES_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = names[page * CATEGORIES_PER_PAGE : (page + 1) * CATEGORIES_PER_PAGE]
    rows = [[_btn(f"✅ {n}" if n == current else n, "catset", category_key(n))] for n in chunk]
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", "cat", str(page - 1)))
    if pages > 1:
        nav.append(_btn(f"{page + 1}/{pages}", "cat", str(page)))
    if page < pages - 1:
        nav.append(_btn("➡️", "cat", str(page + 1)))
    if nav:
        rows.append(nav)
    rows.append(back_row("category" if current else None))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def edit_or_send(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


async def send_screen(bot: Bot, chat_id: int, params: dict) -> None:
    text, markup = render_screen(params)
    await bot.send_message(chat_id, text, reply_markup=markup)


# ---------- router ----------


def create_router() -> Router:
    router = Router(name="search")
    router.message.filter(filters.registered)
    router.callback_query.filter(filters.registered)

    @router.message(F.text == t.BTN_SEARCH)
    async def on_open_from_menu(
        message: Message, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        await state.clear()
        await events.log_event(session, events.SEARCH_STARTED, user_id=user.id)
        params = await search_svc.get_draft(session, user.id)
        await send_screen(message.bot, message.chat.id, params)

    @router.callback_query(SCb.filter(F.a == "open"))
    async def on_open(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        await state.clear()
        params = await search_svc.get_draft(session, user.id)
        await edit_or_send(callback, *render_screen(params))
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "cancel"))
    async def on_cancel(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        await state.clear()
        params = await search_svc.get_draft(session, user.id)
        await edit_or_send(callback, *render_grid(params))
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "menu"))
    async def on_menu(callback: CallbackQuery, state: FSMContext, roles: set[str]) -> None:
        await state.clear()
        await callback.answer()
        await callback.bot.send_message(
            callback.from_user.id, t.MAIN_MENU, reply_markup=kb.main_menu(roles)
        )

    @router.callback_query(SCb.filter(F.a == "grid"))
    async def on_grid(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        await state.clear()
        params = await search_svc.get_draft(session, user.id)
        await edit_or_send(callback, *render_grid(params))
        await callback.answer()

    # --- single choice: deal type and auction type ---
    @router.callback_query(SCb.filter(F.a.in_({"deal", "type"})))
    async def on_choice(
        callback: CallbackQuery, callback_data: SCb, session: AsyncSession, user: User
    ) -> None:
        field = "auction_type" if callback_data.a == "deal" else "start_price_type"
        title = t.CHOOSE_DEAL if field == "auction_type" else t.CHOOSE_TYPE
        current = (await search_svc.get_draft(session, user.id)).get(field)
        rows = [
            [_btn(f"✅ {label}" if code == current else label, "set", f"{field}={code}")]
            for code, label in SINGLE_CHOICE[field].items()
        ]
        rows.append(back_row(field if current else None))
        await edit_or_send(callback, title, InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "set"))
    async def on_set(
        callback: CallbackQuery, callback_data: SCb, session: AsyncSession, user: User
    ) -> None:
        field, _, value = callback_data.v.partition("=")
        if field not in SINGLE_CHOICE or (value and value not in SINGLE_CHOICE[field]):
            await callback.answer(t.STALE_ACTION)
            return
        params = await search_svc.update_draft(session, user.id, **{field: value or None})
        await edit_or_send(callback, *render_screen(params))
        await callback.answer()

    # --- category ---
    @router.callback_query(SCb.filter(F.a == "cat"))
    async def on_categories(
        callback: CallbackQuery,
        callback_data: SCb,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        try:
            names = await tender.get_categories()
        except TenderError:
            log.exception("Failed to load categories")
            await callback.answer(t.CATEGORIES_UNAVAILABLE, show_alert=True)
            return
        current = (await search_svc.get_draft(session, user.id)).get("category")
        page = int(callback_data.v or 0) if callback_data.v.isdigit() else 0
        await edit_or_send(callback, t.CHOOSE_CATEGORY, categories_keyboard(names, page, current))
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "catset"))
    async def on_category_set(
        callback: CallbackQuery,
        callback_data: SCb,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
    ) -> None:
        value = None
        if callback_data.v:
            try:
                names = await tender.get_categories()
            except TenderError:
                await callback.answer(t.CATEGORIES_UNAVAILABLE, show_alert=True)
                return
            matches = [n for n in names if category_key(n) == callback_data.v]
            if not matches:
                await callback.answer(t.STALE_ACTION)
                return
            value = matches[0]
        params = await search_svc.update_draft(session, user.id, category=value)
        await edit_or_send(callback, *render_screen(params))
        await callback.answer()

    # --- regions (multi-select) ---
    @router.callback_query(SCb.filter(F.a == "reg"))
    async def on_regions(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
        selected = (await search_svc.get_draft(session, user.id)).get("regions") or []
        await edit_or_send(callback, t.CHOOSE_REGIONS, regions_keyboard(selected))
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "regt"))
    async def on_region_toggle(
        callback: CallbackQuery, callback_data: SCb, session: AsyncSession, user: User
    ) -> None:
        if not callback_data.v.isdigit() or int(callback_data.v) >= len(REGIONS):
            await callback.answer(t.STALE_ACTION)
            return
        region = REGIONS[int(callback_data.v)]
        draft = await search_svc.get_draft(session, user.id, lock=True)
        selected = list(draft.get("regions") or [])
        if region in selected:
            selected.remove(region)
        else:
            selected.append(region)
        params = await search_svc.update_draft(session, user.id, regions=selected or None)
        await edit_or_send(callback, t.CHOOSE_REGIONS, regions_keyboard(params.get("regions", [])))
        await callback.answer()

    # --- free-text fields ---
    @router.callback_query(SCb.filter(F.a == "txt"))
    async def on_text_prompt(
        callback: CallbackQuery,
        callback_data: SCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
    ) -> None:
        field = callback_data.v
        if field not in TEXT_PROMPTS:
            await callback.answer(t.STALE_ACTION)
            return
        current = (await search_svc.get_draft(session, user.id)).get(field)
        await state.set_state(SearchInput.text)
        await state.set_data({"field": field})
        prompt = TEXT_PROMPTS[field]
        if current:
            prompt += f"\n\nЗараз: <b>{html.escape(current)}</b>"
        await edit_or_send(
            callback, prompt, input_keyboard(clear_target=field if current else None)
        )
        await callback.answer()

    @router.message(SearchInput.text, F.text)
    async def on_text_input(
        message: Message, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        data = await state.get_data()
        field = data.get("field")
        value = (message.text or "").strip()
        if field not in TEXT_PROMPTS:
            await state.clear()
            return
        if not value or value.startswith("/") or len(value) > MAX_TEXT_LEN:
            await message.answer(t.BAD_TEXT, reply_markup=input_keyboard())
            return
        params = await search_svc.update_draft(session, user.id, **{field: value})
        await state.clear()
        await send_screen(message.bot, message.chat.id, params)

    @router.callback_query(SCb.filter(F.a == "fclr"))
    async def on_field_clear(
        callback: CallbackQuery,
        callback_data: SCb,
        state: FSMContext,
        session: AsyncSession,
        user: User,
    ) -> None:
        target = callback_data.v
        await state.clear()
        if target in TEXT_PROMPTS or target in CLEARABLE:
            changes = {target: None}
        elif target == "price":
            changes = {"min_price": None, "max_price": None}
        elif target == "area":
            changes = {"area_unit": None, "min_area": None, "max_area": None}
        else:
            await callback.answer(t.STALE_ACTION)
            return
        params = await search_svc.update_draft(session, user.id, **changes)
        await edit_or_send(callback, *render_screen(params))
        await callback.answer()

    # --- price / area ranges ---
    @router.callback_query(SCb.filter(F.a == "price"))
    async def on_price(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        p = normalize(await search_svc.get_draft(session, user.id))
        await state.set_state(SearchInput.min_value)
        await state.set_data({"range": "price"})
        has = "min_price" in p or "max_price" in p
        await edit_or_send(
            callback,
            t.PROMPT_MIN_PRICE,
            input_keyboard(skip=True, clear_target="price" if has else None),
        )
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "area"))
    async def on_area(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
        p = normalize(await search_svc.get_draft(session, user.id))
        rows = [
            [_btn(f"{label}", "unit", code)]
            for code, label in (("ha.", "Гектари (га)"), ("sq.m.", "Квадратні метри (м²)"))
        ]
        rows.append(back_row("area" if "area_unit" in p else None))
        await edit_or_send(callback, t.CHOOSE_AREA_UNIT, InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "unit"))
    async def on_area_unit(callback: CallbackQuery, callback_data: SCb, state: FSMContext) -> None:
        if callback_data.v not in AREA_UNITS:
            await callback.answer(t.STALE_ACTION)
            return
        await state.set_state(SearchInput.min_value)
        await state.set_data({"range": "area", "unit": callback_data.v})
        unit = AREA_UNITS[callback_data.v]
        await edit_or_send(callback, t.PROMPT_MIN_AREA.format(unit=unit), input_keyboard(skip=True))
        await callback.answer()

    async def _after_min(
        bot: Bot,
        chat_id: int,
        state: FSMContext,
        min_value: str | None,
        edit: CallbackQuery | None = None,
    ) -> None:
        data = await state.get_data()
        data["min"] = min_value
        await state.set_data(data)
        await state.set_state(SearchInput.max_value)
        if data["range"] == "price":
            prompt = t.PROMPT_MAX_PRICE
        else:
            prompt = t.PROMPT_MAX_AREA.format(unit=AREA_UNITS[data["unit"]])
        if edit is not None:
            await edit_or_send(edit, prompt, input_keyboard(skip=True))
        else:
            await bot.send_message(chat_id, prompt, reply_markup=input_keyboard(skip=True))

    async def _finish_range(
        bot: Bot,
        chat_id: int,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        max_value: str | None,
        edit: CallbackQuery | None = None,
    ) -> None:
        data = await state.get_data()
        min_value = data.get("min")
        await state.clear()
        if data["range"] == "price":
            changes = {"min_price": min_value, "max_price": max_value}
        else:
            changes = {"area_unit": data["unit"], "min_area": min_value, "max_area": max_value}
            if min_value is None and max_value is None:
                changes["area_unit"] = None
        params = await search_svc.update_draft(session, user.id, **changes)
        text, markup = render_screen(params)
        if min_value is None and max_value is None:
            text = f"{t.RANGE_NOT_SET}\n\n{text}"
        if edit is not None:
            await edit_or_send(edit, text, markup)
        else:
            await bot.send_message(chat_id, text, reply_markup=markup)

    @router.message(SearchInput.min_value, F.text)
    async def on_min_input(message: Message, state: FSMContext) -> None:
        try:
            value = parse_number(message.text or "")
        except ValueError:
            await message.answer(t.BAD_NUMBER, reply_markup=input_keyboard(skip=True))
            return
        await _after_min(message.bot, message.chat.id, state, str(value))

    @router.message(SearchInput.max_value, F.text)
    async def on_max_input(
        message: Message, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        try:
            value = parse_number(message.text or "")
        except ValueError:
            await message.answer(t.BAD_NUMBER, reply_markup=input_keyboard(skip=True))
            return
        min_value = (await state.get_data()).get("min")
        if min_value is not None and value < Decimal(min_value):
            await message.answer(
                t.BAD_RANGE.format(min=format_number(min_value)),
                reply_markup=input_keyboard(skip=True),
            )
            return
        await _finish_range(message.bot, message.chat.id, state, session, user, str(value))

    @router.callback_query(SCb.filter(F.a == "skip"))
    async def on_skip(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        current = await state.get_state()
        if current == SearchInput.min_value.state:
            await _after_min(callback.bot, callback.from_user.id, state, None, edit=callback)
        elif current == SearchInput.max_value.state:
            await _finish_range(
                callback.bot, callback.from_user.id, state, session, user, None, edit=callback
            )
        else:
            params = await search_svc.get_draft(session, user.id)
            await edit_or_send(callback, *render_screen(params))
        await callback.answer()

    # --- clear all ---
    @router.callback_query(SCb.filter(F.a == "clear"))
    async def on_clear(callback: CallbackQuery) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[_btn(t.BTN_YES_CLEAR, "clearok"), _btn(t.BTN_NO, "open")]]
        )
        await edit_or_send(callback, t.CONFIRM_CLEAR, markup)
        await callback.answer()

    @router.callback_query(SCb.filter(F.a == "clearok"))
    async def on_clear_ok(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        await state.clear()
        await search_svc.clear_draft(session, user.id)
        await edit_or_send(callback, *render_screen({}))
        await callback.answer()

    # --- run ---
    @router.callback_query(SCb.filter(F.a == "run"))
    async def on_run(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        user: User,
        tender: TenderClient,
        settings: Settings,
    ) -> None:
        from app.bot.handlers.results import send_results_page

        await state.clear()
        params = await search_svc.get_draft(session, user.id)
        if not is_effective(params):
            await callback.answer(t.SEARCH_NEED_PARAM, show_alert=True)
            return
        await callback.answer()
        snapshot = await search_svc.create_snapshot(session, user.id, params)
        await send_results_page(
            callback.bot, callback.from_user.id, session, settings, tender, user, snapshot, 1
        )

    return router
