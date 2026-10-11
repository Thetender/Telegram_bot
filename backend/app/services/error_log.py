"""Store ERROR log records in the database so admins can read them in the
Dashboard (Журнал помилок) without server access. Secrets are redacted by
the same rules as the console log. Best effort: never breaks logging."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.logging_setup import redact

KEEP_DAYS = 30
MAX_DETAILS = 8000

_sf: async_sessionmaker[AsyncSession] | None = None
_tasks: set[asyncio.Task] = set()


class DatabaseErrorHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if _sf is None or record.name.startswith(("sqlalchemy", "app.services.error_log")):
            return
        try:
            message = redact(record.getMessage())[:2000]
            details = None
            if record.exc_info:
                details = redact("".join(traceback.format_exception(*record.exc_info)))
                details = details[-MAX_DETAILS:]
            loop = asyncio.get_running_loop()
        except Exception:  # noqa: BLE001 - no loop / formatting problem
            return
        task = loop.create_task(_store(record.levelname, record.name[:128], message, details))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _store(level: str, logger_name: str, message: str, details: str | None) -> None:
    from app.models import AppError

    if _sf is None:
        return
    try:
        async with _sf() as session:
            session.add(AppError(level=level, logger=logger_name, message=message, details=details))
            await session.execute(
                delete(AppError).where(
                    AppError.created_at < datetime.now(UTC) - timedelta(days=KEEP_DAYS)
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        pass


_handler = DatabaseErrorHandler()


def configure(session_factory: async_sessionmaker[AsyncSession] | None) -> None:
    global _sf
    _sf = session_factory
    root = logging.getLogger()
    if session_factory is not None and _handler not in root.handlers:
        root.addHandler(_handler)


async def drain() -> None:
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
