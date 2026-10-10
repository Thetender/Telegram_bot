"""The Tender new-auction webhook → durable queue → delivery worker (FR §7.1)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import func, select, update

from app.api.main import THETENDER_WEBHOOK_PATH, create_app
from app.integrations.thetender import MockTenderClient
from app.logging_setup import RedactSecretsFilter
from app.models import Event, NotificationDelivery, User
from app.services import access
from app.workers import delivery as delivery_mod
from app.workers.delivery import DeliveryWorker
from tests.conftest import make_settings
from tests.fake_telegram import FakeSession, contact_update, make_bot

KEY = "tg-api-key-123"
U1, U2, U3 = 7001, 7002, 7003


def event(number: str = "LSE001-UA-20261010-00001", users=None, **auction) -> dict:
    data = {
        "name": "Нежитлове приміщення <площею> 50 м²",
        "number": number,
        "companyName": "ДП «Тест»",
        "price": "125000.50",
        "url": f"https://thetender.com.ua/auction/{number}?utm_source=telegram",
        **auction,
    }
    return {
        "event_type": "new_auction",
        "auction_data": data,
        "users": users if users is not None else [{str(U1): "Склади Київ"}],
    }


@dataclass
class Env:
    client: httpx.AsyncClient
    tg: FakeSession
    sf: object
    worker: DeliveryWorker
    app: object

    async def post(self, payload, auth: str = KEY) -> httpx.Response:
        return await self.client.post(
            THETENDER_WEBHOOK_PATH, params={"auth": auth} if auth else None, json=payload
        )

    def sends(self, chat_id: int | None = None) -> list[SendMessage]:
        return [
            m
            for m in self.tg.requests
            if isinstance(m, SendMessage) and (chat_id is None or int(m.chat_id) == chat_id)
        ]

    async def statuses(self) -> dict[int, str]:
        async with self.sf() as s:
            rows = await s.execute(
                select(NotificationDelivery.telegram_user_id, NotificationDelivery.status)
            )
            return dict(rows.all())


@asynccontextmanager
async def environment(session_factory):
    bot, tg = make_bot()
    settings = make_settings(
        public_base_url="https://tg.example.com",
        telegram_webhook_secret="s3cret",
        thetender_api_key=KEY,
    )
    app = create_app(settings, bot=bot, tender=MockTenderClient())
    async with app.router.lifespan_context(app):
        worker = DeliveryWorker(
            bot, session_factory, settings, global_interval=0, per_chat_interval=0
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://tg.example.com") as c:
            env = Env(client=c, tg=tg, sf=session_factory, worker=worker, app=app)
            for uid in (U1, U2):
                await app.state.dp.feed_update(
                    bot, contact_update(uid, contact_user_id=uid, phone=f"38067{uid:07d}")
                )
            tg.clear()
            yield env


async def test_auth_is_required(session_factory):
    async with environment(session_factory) as env:
        assert (await env.post(event(), auth="")).status_code == 403
        assert (await env.post(event(), auth="wrong")).status_code == 403
        assert await env.statuses() == {}


async def test_persist_then_deliver_once_with_tracked_link(session_factory):
    async with environment(session_factory) as env:
        r = await env.post(event(users=[{str(U1): "Склади Київ"}, {str(U2): "Все підряд"}]))
        assert r.status_code == 200 and r.json() == {"ok": True, "accepted": 2}
        assert env.sends() == []  # nothing is sent inside the webhook request
        assert await env.statuses() == {U1: "PENDING", U2: "PENDING"}

        # Webhook retry (same auction/users) is a no-op.
        r = await env.post(event(users=[{str(U1): "Склади Київ"}, {str(U2): "Все підряд"}]))
        assert r.json() == {"ok": True, "accepted": 0}

        assert await env.worker.run_once() == 2
        assert await env.statuses() == {U1: "SENT", U2: "SENT"}
        (msg,) = env.sends(U1)
        assert msg.text.startswith("🔔 <b>Новий аукціон</b>")
        assert "Моніторинг: <b>Склади Київ</b>" in msg.text
        assert "&lt;площею&gt;" in msg.text  # external text is escaped
        assert "💰 Стартова ціна: 125\xa0000,50 грн" in msg.text
        assert 'href="https://tg.example.com/r/' in msg.text  # signed tracked redirect
        assert await env.worker.run_once() == 0

        # Even after delivery, a late retry from The Tender never re-sends.
        await env.post(event(users=[{str(U1): "Склади Київ"}]))
        await env.worker.run_once()
        assert len(env.sends(U1)) == 1
        async with env.sf() as s:
            sent = await s.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.event_type == "AUCTION_NOTIFICATION_SENT")
            )
            assert sent == 2


async def test_unknown_blocked_and_unreachable_users(session_factory):
    async with environment(session_factory) as env:
        async with env.sf() as s:
            u2 = await s.scalar(select(User).where(User.telegram_id == U2))
            admin = User(telegram_id=1, name="admin", phone="+380000000001")
            s.add(admin)
            await s.flush()
            await access.block_user(s, u2, admin, "конкурент")
            await s.commit()
        env.tg.fail_chats.add(U1)  # U1 blocked the bot in Telegram
        await env.post(
            event(users=[{str(U1): "a"}, {str(U2): "b"}, {str(U3): "c"}, {"not-a-number": "x"}])
        )
        await env.worker.run_once()
        assert await env.statuses() == {U1: "FAILED", U2: "SKIPPED", U3: "SKIPPED"}
        assert env.sends() == []
        async with env.sf() as s:
            u1 = await s.scalar(select(User).where(User.telegram_id == U1))
            assert u1.bot_blocked_at is not None  # unreachable: skipped by campaigns
        assert await env.worker.run_once() == 0  # no retry loop


async def test_flood_limit_pauses_and_transient_errors_retry(session_factory, monkeypatch):
    async with environment(session_factory) as env:
        env.tg.raise_next = [
            TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=0),
        ]
        await env.post(event(users=[{str(U1): "a"}, {str(U2): "b"}]))
        await env.worker.run_once()  # 429 on the first message: both go back to the queue
        assert await env.statuses() == {U1: "PENDING", U2: "PENDING"}
        await env.worker.run_once()
        assert await env.statuses() == {U1: "SENT", U2: "SENT"}

        env.tg.raise_next = [TelegramNetworkError(method=None, message="connection reset")]
        await env.post(event(number="LSE-2", users=[{str(U1): "a"}]))
        await env.worker.run_once()
        async with env.sf() as s:
            d = await s.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.canonical_auction_id == "LSE-2"
                )
            )
            assert d.status == "PENDING" and d.attempts == 1 and d.next_attempt_at > datetime.now(
                UTC
            )
            # Pretend the backoff passed.
            await s.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.id == d.id)
                .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await s.commit()
        await env.worker.run_once()
        statuses = await env.statuses()
        assert len(env.sends(U1)) == 2 and statuses[U1] == "SENT"


async def test_stuck_sending_rows_are_recovered_after_crash(session_factory):
    async with environment(session_factory) as env:
        await env.post(event())
        async with env.sf() as s:
            await s.execute(
                update(NotificationDelivery).values(
                    status="SENDING", locked_at=datetime.now(UTC) - timedelta(minutes=10)
                )
            )
            await s.commit()
        await env.worker.run_once()
        assert await env.statuses() == {U1: "SENT"}


async def test_burst_of_200_users_is_acknowledged_fast(session_factory):
    async with environment(session_factory) as env:
        users = [{str(100000 + i): "m"} for i in range(200)]
        loop = asyncio.get_running_loop()
        started = loop.time()
        r = await env.post(event(users=users))
        assert r.json()["accepted"] == 200
        assert loop.time() - started < 5
        async with env.sf() as s:
            assert await s.scalar(select(func.count()).select_from(NotificationDelivery)) == 200


async def test_payload_variants_and_ignored_events(session_factory):
    async with environment(session_factory) as env:
        r = await env.post({"event_type": "something_else"})
        assert r.json() == {"ok": True, "ignored": True}
        r = await env.post({"event_type": "new_auction", "auction_data": {"name": "no id"}})
        assert r.status_code == 200 and r.json()["accepted"] == 0  # dropped, no retry loop
        # Mapping instead of a list; the same user twice counts once.
        r = await env.post(event(number="LSE-3", users={str(U1): "a"}))
        assert r.json()["accepted"] == 1
        bad = await env.client.post(
            THETENDER_WEBHOOK_PATH, params={"auth": KEY}, content=b"{not json"
        )
        assert bad.status_code == 400


def test_api_key_is_redacted_from_access_logs():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "POST", f"{THETENDER_WEBHOOK_PATH}?auth={KEY}", "1.1", 200),
        None,
    )
    RedactSecretsFilter().filter(record)
    assert KEY not in record.getMessage() and "auth=***" in record.getMessage()


def test_backoff_is_bounded():
    assert delivery_mod.backoff(1) == timedelta(seconds=30)
    assert delivery_mod.backoff(20) == timedelta(hours=1)


async def test_background_worker_in_app_lifespan_delivers(session_factory):
    """In production the worker runs inside the app and is woken by the webhook."""
    bot, tg = make_bot()
    settings = make_settings(
        public_base_url="https://tg.example.com",
        telegram_webhook_secret="s3cret",
        thetender_api_key=KEY,
        delivery_worker_enabled=True,
    )
    app = create_app(settings, bot=bot, tender=MockTenderClient())
    async with app.router.lifespan_context(app):
        await app.state.dp.feed_update(bot, contact_update(U1, contact_user_id=U1))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://tg.example.com") as c:
            await c.post(THETENDER_WEBHOOK_PATH, params={"auth": KEY}, json=event())
        for _ in range(50):
            async with session_factory() as s:
                status = await s.scalar(select(NotificationDelivery.status))
            if status == "SENT":
                break
            await asyncio.sleep(0.1)
        assert status == "SENT"
