from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand, LinkPreviewOptions
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import (
    admin_invite,
    consultation,
    fallback,
    help,
    menu,
    monitorings,
    results,
    search,
    start,
)
from app.bot.middlewares import UpdateContextMiddleware
from app.bot.storage import PostgresStorage
from app.config import Settings
from app.integrations.thetender import TenderClient, make_tender_client

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Почати"),
    BotCommand(command="menu", description="Головне меню"),
]


class RetryConnectErrors(BaseRequestMiddleware):
    """Retry a Telegram call when the connection could not even be opened
    (e.g. «Connection reset by peer» on connect). The request never reached
    Telegram then, so a retry cannot duplicate a message."""

    def __init__(self, attempts: int = 3, delay: float = 0.5) -> None:
        self.attempts = attempts
        self.delay = delay

    async def __call__(self, make_request, bot, method):  # type: ignore[no-untyped-def]
        for attempt in range(1, self.attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramNetworkError as exc:
                if "Cannot connect to host" not in str(exc) or attempt == self.attempts:
                    raise
                log.info("Telegram unreachable (%s), retry %s", exc, attempt)
                await asyncio.sleep(self.delay * attempt)
        raise AssertionError("unreachable")


def create_bot(settings: Settings, **kwargs) -> Bot:
    if "session" not in kwargs:
        session = AiohttpSession()
        session.middleware(RetryConnectErrors())
        kwargs["session"] = session
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
        monitorings.create_router(),
        admin_invite.create_router(),
        help.create_router(),
        fallback.create_router(),
    )
    return dp


async def configure_bot(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
