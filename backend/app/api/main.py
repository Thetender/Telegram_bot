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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

from app.admin import mount_admin
from app.bot.factory import configure_bot, create_bot, create_dispatcher
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.integrations.thetender import TenderClient, make_tender_client
from app.logging_setup import setup_logging
from app.models import User
from app.services import events
from app.services.links import allowed_hosts, is_allowed_destination, is_bot_user_agent, parse_token

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    bot: Bot | None = None,
    tender: TenderClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.validate_runtime()
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.bot = bot or create_bot(settings)
        app.state.tender = tender or make_tender_client(settings)
        app.state.dp = create_dispatcher(settings, session_factory, app.state.tender)
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
            await app.state.tender.aclose()
            await engine.dispose()

    app = FastAPI(
        title="The Tender Telegram service", lifespan=lifespan, docs_url=None, redoc_url=None,
        openapi_url=None,
    )
    mount_admin(app)

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

    allowed = allowed_hosts(settings.thetender_base_url)

    @app.api_route("/r/{token}", methods=["GET", "HEAD"])
    async def tracked_redirect(token: str, request: Request) -> Response:
        """Signed auction link: log AUCTION_OPENED and redirect (FR §6.1).

        HEAD requests and link-preview/bot fetches are redirected without
        being counted as clicks. Works regardless of legal re-acceptance."""
        key = settings.link_signing_key
        target = parse_token(key, token) if key else None
        if target is None or not is_allowed_destination(target.url, allowed):
            return HTMLResponse(INVALID_LINK_PAGE, status_code=404)
        if request.method == "GET" and not is_bot_user_agent(request.headers.get("user-agent")):
            try:
                async with request.app.state.session_factory() as session:
                    user = await session.get(User, target.user_id)
                    await events.log_event(
                        session,
                        events.AUCTION_OPENED,
                        user_id=user.id if user else None,
                        data={
                            "source": target.source,
                            "auction_number": target.auction_number,
                            "url": target.url,
                        },
                    )
                    await session.commit()
            except Exception:  # noqa: BLE001 - analytics must never block the redirect
                log.exception("Failed to log AUCTION_OPENED")
        return RedirectResponse(target.url, status_code=302)

    return app


INVALID_LINK_PAGE = """<!doctype html><html lang="uk"><meta charset="utf-8">
<title>Посилання недійсне</title>
<body style="font-family:sans-serif;padding:2em">
<h3>Посилання недійсне</h3><p>Відкрийте аукціон заново з повідомлення бота.</p></body></html>"""
