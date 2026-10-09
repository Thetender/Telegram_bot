"""FastAPI application: health check and Telegram webhook endpoint.

Later iterations add: The Tender notification webhook, signed redirect
endpoint for click tracking and the Admin Dashboard API.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.bot.factory import configure_bot, create_bot, create_dispatcher
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.logging_setup import setup_logging

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, bot: Bot | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.validate_runtime()
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.bot = bot or create_bot(settings)
        app.state.dp = create_dispatcher(settings, session_factory)
        if settings.bot_mode == "webhook":
            assert settings.telegram_webhook_secret is not None
            url = settings.public_base_url + settings.telegram_webhook_path
            await app.state.bot.set_webhook(
                url=url,
                secret_token=settings.telegram_webhook_secret.get_secret_value(),
                allowed_updates=app.state.dp.resolve_used_update_types(),
            )
            await configure_bot(app.state.bot)
            log.info("Telegram webhook set to %s", url)
        try:
            yield
        finally:
            await app.state.bot.session.close()
            await engine.dispose()

    app = FastAPI(title="The Tender Telegram service", lifespan=lifespan, docs_url=None)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        db_ok = True
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            log.exception("Health check: database unavailable")
            db_ok = False
        body = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "unavailable",
            "environment": settings.environment,
            "version": os.environ.get("APP_VERSION", "dev"),
        }
        return JSONResponse(body, status_code=200 if db_ok else 503)

    @app.post(settings.telegram_webhook_path)
    async def telegram_webhook(request: Request) -> Response:
        expected = (
            settings.telegram_webhook_secret.get_secret_value()
            if settings.telegram_webhook_secret
            else ""
        )
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected or not hmac.compare_digest(received, expected):
            return Response(status_code=403)
        bot_: Bot = request.app.state.bot
        dp: Dispatcher = request.app.state.dp
        update = Update.model_validate(await request.json(), context={"bot": bot_})
        try:
            await dp.feed_update(bot_, update)
        except Exception:  # noqa: BLE001
            # The update is already recorded as processed; answering 200 avoids
            # Telegram re-sending it endlessly. The error goes to logs/alerts.
            log.exception("Error while handling Telegram update %s", update.update_id)
        return Response(status_code=200)

    return app
