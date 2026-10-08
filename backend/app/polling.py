"""Local development runner: long polling, no public HTTPS needed.

    python -m app.polling

Uses the separate development bot token from .env. Never run it with the
production token: polling would steal production updates.
"""

from __future__ import annotations

import asyncio
import logging

from app.bot.factory import configure_bot, create_bot, create_dispatcher
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.logging_setup import setup_logging

log = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.environment == "prod":
        raise SystemExit("Polling mode is for local development only")
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    bot = create_bot(settings)
    dp = create_dispatcher(settings, sf)
    await bot.delete_webhook(drop_pending_updates=False)
    await configure_bot(bot)
    log.info("Starting long polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
