from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, LinkPreviewOptions
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import (
    admin_invite,
    consultation,
    fallback,
    help,
    menu,
    results,
    search,
    start,
)
from app.bot.middlewares import UpdateContextMiddleware
from app.bot.storage import PostgresStorage
from app.config import Settings
from app.integrations.thetender import TenderClient, make_tender_client

BOT_COMMANDS = [
    BotCommand(command="start", description="Почати"),
    BotCommand(command="menu", description="Головне меню"),
]


def create_bot(settings: Settings, **kwargs) -> Bot:
    return Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            # Link previews are noisy and make preview-fetches look like clicks.
            link_preview=LinkPreviewOptions(is_disabled=True),
        ),
        **kwargs,
    )


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    tender: TenderClient | None = None,
) -> Dispatcher:
    dp = Dispatcher(storage=PostgresStorage(session_factory))
    dp["settings"] = settings
    dp["tender"] = tender or make_tender_client(settings)
    dp.update.outer_middleware(UpdateContextMiddleware(session_factory))
    # Order matters: start/registration → menu (works in any state) → features → fallback.
    dp.include_routers(
        start.create_router(),
        menu.create_router(),
        search.create_router(),
        results.create_router(),
        consultation.create_router(),
        admin_invite.create_router(),
        help.create_router(),
        fallback.create_router(),
    )
    return dp


async def configure_bot(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
