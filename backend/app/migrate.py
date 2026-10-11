"""Startup migrations: `python -m app.migrate` (used by the container).

Runs `alembic upgrade head`. If the database is already at a NEWER revision
than this code knows (a rollback to a previous release), it does nothing and
lets the app start: our migrations only add tables/columns, so an older
version keeps working with a newer schema.
"""

from __future__ import annotations

import asyncio
import os
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def current_revision(url: str) -> str | None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(text("SELECT to_regclass('public.alembic_version')"))
            if exists is None:
                return None
            return await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    finally:
        await engine.dispose()


def main() -> int:
    cfg = Config(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    known = {rev.revision for rev in script.walk_revisions()}
    current = asyncio.run(current_revision(os.environ["DATABASE_URL"]))
    if current is not None and current not in known:
        print(
            f"Database schema revision {current} is newer than this release "
            f"(head {script.get_current_head()}): skipping migrations (rollback mode)."
        )
        return 0
    command.upgrade(cfg, "head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
