from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.factory import create_dispatcher
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.integrations.thetender import MockTenderClient
from app.models import Base
from tests.fake_telegram import FakeSession, make_bot

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/tg_test"
)
BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _migrate_database() -> None:
    """Build the schema with the real Alembic migrations (not create_all),
    so migrations are tested on every run."""
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            check=True,
            capture_output=True,
        )


def make_settings(**overrides) -> Settings:
    data = {
        "environment": "test",
        "telegram_bot_token": "42:TEST_TOKEN_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "database_url": TEST_DATABASE_URL,
        "bot_mode": "polling",
    }
    data.update(overrides)
    return Settings.model_validate(data)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(TEST_DATABASE_URL)
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield make_session_factory(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@dataclass
class BotHarness:
    bot: Bot
    tg: FakeSession
    dp: Dispatcher
    sf: async_sessionmaker[AsyncSession]
    tender: MockTenderClient

    async def feed(self, update: Update) -> None:
        await self.dp.feed_update(self.bot, update)


@pytest_asyncio.fixture
async def harness(session_factory) -> BotHarness:
    bot, tg = make_bot()
    tender = MockTenderClient()
    settings = make_settings(
        public_base_url="https://tg-test.example.com", telegram_webhook_secret="s3cret"
    )
    dp = create_dispatcher(settings, session_factory, tender)
    return BotHarness(bot=bot, tg=tg, dp=dp, sf=session_factory, tender=tender)
