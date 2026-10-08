from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from aiogram.methods import SetWebhook
from sqlalchemy import select

from app.api.main import create_app
from app.models import ROLE_ADMIN, AdminSettings, Event, LegalDocumentVersion, User
from app.services import legal
from app.services import users as users_svc
from tests.conftest import make_settings
from tests.fake_telegram import contact_update, make_bot


class LifespanClient:
    """httpx client that also runs the FastAPI lifespan (startup/shutdown)."""

    def __init__(self, app):
        self.app = app

    async def __aenter__(self) -> httpx.AsyncClient:
        self._ctx = self.app.router.lifespan_context(self.app)
        await self._ctx.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        return self.client

    async def __aexit__(self, *exc):
        await self.client.aclose()
        await self._ctx.__aexit__(*exc)


async def test_health_ok(session_factory):
    bot, _ = make_bot()
    app = create_app(make_settings(), bot=bot)
    async with LifespanClient(app) as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "ok"


async def test_webhook_mode_sets_webhook_and_checks_secret(session_factory):
    bot, tg = make_bot()
    settings = make_settings(
        bot_mode="webhook",
        public_base_url="https://tg-test.example.com/",
        telegram_webhook_secret="s3cret",
    )
    app = create_app(settings, bot=bot)
    async with LifespanClient(app) as client:
        set_hooks = [r for r in tg.requests if isinstance(r, SetWebhook)]
        assert set_hooks[0].url == "https://tg-test.example.com/telegram/webhook"
        assert set_hooks[0].secret_token == "s3cret"

        update = contact_update(3003, contact_user_id=3003).model_dump(
            mode="json", exclude_none=True
        )
        r = await client.post("/telegram/webhook", json=update)
        assert r.status_code == 403  # no secret header
        r = await client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert r.status_code == 403
        r = await client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
        assert r.status_code == 200

    async with session_factory() as s:
        assert await s.scalar(select(User).where(User.telegram_id == 3003)) is not None


def test_non_prod_cannot_use_production_api():
    settings = make_settings(thetender_base_url="https://thetender.com.ua")
    with pytest.raises(ValueError):
        settings.validate_runtime()


def test_webhook_mode_requires_secret():
    settings = make_settings(bot_mode="webhook", public_base_url="https://x.example.com")
    with pytest.raises(ValueError):
        settings.validate_runtime()


async def test_grant_admin_is_idempotent_and_audited(db):
    db.add(User(telegram_id=777, phone="+380671111111"))
    await db.commit()
    user = await db.scalar(select(User).where(User.telegram_id == 777))
    assert await users_svc.grant_role(db, user, ROLE_ADMIN, actor_user_id=None) is True
    assert await users_svc.grant_role(db, user, ROLE_ADMIN, actor_user_id=None) is False
    await db.commit()
    assert await users_svc.count_admins(db) == 1
    assert await db.get(AdminSettings, user.id) is not None
    audit = (await db.scalars(select(Event).where(Event.event_type == "ROLE_GRANTED"))).all()
    assert len(audit) == 1 and audit[0].event_data["new"] == ROLE_ADMIN


async def test_publish_legal_version_archives_previous(db):
    now = datetime.now(UTC)
    v1 = await legal.publish_version(db, legal.TERMS, "1.0", "https://a/terms", "t1.pdf", now)
    v2 = await legal.publish_version(db, legal.TERMS, "1.1", "https://a/terms", "t2.pdf", now)
    await db.commit()
    await db.refresh(v1)
    assert v1.status == "ARCHIVED" and v2.status == "ACTIVE"
    active = await legal.get_active_versions(db)
    assert active[legal.TERMS].version == "1.1"

    with pytest.raises(ValueError):
        await legal.publish_version(db, legal.TERMS, "1.1", "https://b", "x.pdf", now)
    await db.rollback()
    count = len((await db.scalars(select(LegalDocumentVersion))).all())
    assert count == 2


def test_log_filter_redacts_bot_token(caplog):
    import logging

    from app.logging_setup import RedactSecretsFilter

    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1,
        "calling https://api.telegram.org/bot123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ/sendMessage",
        None, None,
    )
    RedactSecretsFilter().filter(record)
    assert "AAbbCC" not in record.getMessage()


async def test_cli_grant_admin_by_phone(db, monkeypatch):
    from app import cli
    from app.config import get_settings
    from tests.conftest import TEST_DATABASE_URL

    db.add(User(telegram_id=888, phone="+380671234567"))
    await db.commit()
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    get_settings.cache_clear()
    try:
        assert await cli.grant_admin(None, "067 123 45 67") == 0
        assert await cli.grant_admin(None, "+380500000000") == 1
    finally:
        get_settings.cache_clear()
    assert await users_svc.count_admins(db) == 1
