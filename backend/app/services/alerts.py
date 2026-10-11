"""Operational alerts to administrators in Telegram.

Admins with «системні сповіщення» ON get a short message when something
needs attention (The Tender rejects our API key, unhandled errors, ...).
Each kind of alert is sent at most once per 5 minutes, so a broken dependency
does not flood the admins. Best effort: alerts are never allowed to break
the code path that raised them.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

THROTTLE_SECONDS = 300.0

_bot: Bot | None = None
_sf: async_sessionmaker[AsyncSession] | None = None
_environment = ""
_last_sent: dict[str, float] = {}
_tasks: set[asyncio.Task] = set()


def configure(
    bot: Bot | None, session_factory: async_sessionmaker[AsyncSession] | None, environment: str = ""
) -> None:
    global _bot, _sf, _environment
    _bot, _sf, _environment = bot, session_factory, environment
    _last_sent.clear()


def alert(kind: str, text: str) -> None:
    """Fire-and-forget; safe to call from anywhere (also without a running bot)."""
    if _bot is None or _sf is None:
        return
    now = time.monotonic()
    if now - _last_sent.get(kind, -THROTTLE_SECONDS) < THROTTLE_SECONDS:
        return
    _last_sent[kind] = now
    try:
        task = asyncio.get_running_loop().create_task(_send(text))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _send(text: str) -> None:
    from app.services.consultations import notification_targets

    assert _bot is not None and _sf is not None
    env = f" [{_environment}]" if _environment and _environment != "prod" else ""
    message = f"⚠️ <b>Технічне сповіщення бота{env}</b>\n\n{text}"
    try:
        async with _sf() as session:
            _, admins = await notification_targets(session)
        for admin in admins:
            try:
                await _bot.send_message(admin.telegram_id, message)
            except Exception as exc:  # noqa: BLE001
                log.info("Alert to admin %s not delivered: %s", admin.id, exc)
    except Exception:  # noqa: BLE001
        log.exception("Failed to send an operational alert")


async def drain() -> None:
    """Wait for alerts in flight (tests, shutdown)."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
