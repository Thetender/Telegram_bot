"""Main menu and /menu (FR §4). Menu actions work in any conversation state
and cancel pending text input, so menu texts are never stored as user input."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import filters
from app.bot import keyboards as kb
from app.bot import texts as t
from app.services import legal


def create_router() -> Router:
    router = Router(name="menu")
    router.message.filter(filters.registered)

    @router.message(Command("menu"))
    async def on_menu(message: Message, state: FSMContext, roles: set[str]) -> None:
        await state.clear()
        await message.answer(t.MAIN_MENU, reply_markup=kb.main_menu(roles))

    @router.message(F.text == t.BTN_SEARCH)
    async def on_search(message: Message, state: FSMContext, roles: set[str]) -> None:
        await state.clear()
        await message.answer(t.coming_soon("Пошук аукціонів"), reply_markup=kb.main_menu(roles))

    @router.message(F.text == t.BTN_MONITORINGS)
    async def on_monitorings(message: Message, state: FSMContext, roles: set[str]) -> None:
        await state.clear()
        await message.answer(t.coming_soon("Мої моніторинги"), reply_markup=kb.main_menu(roles))

    @router.message(F.text == t.BTN_CONSULTATION)
    async def on_consultation(message: Message, state: FSMContext, roles: set[str]) -> None:
        await state.clear()
        await message.answer(t.coming_soon("Консультація"), reply_markup=kb.main_menu(roles))

    @router.message(F.text == t.BTN_MY_REQUESTS, filters.is_manager)
    async def on_my_requests(message: Message, state: FSMContext, roles: set[str]) -> None:
        await state.clear()
        await message.answer(t.coming_soon("Мої заявки"), reply_markup=kb.main_menu(roles))

    @router.message(F.text == t.BTN_HELP)
    async def on_help(message: Message, state: FSMContext, session: AsyncSession) -> None:
        await state.clear()
        versions = await legal.get_active_versions(session)
        await message.answer(t.HELP_TITLE, reply_markup=kb.help_menu(versions))

    return router
