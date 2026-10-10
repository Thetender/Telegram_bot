"""Delivery worker for new-auction notifications (Architecture §10).

PostgreSQL is the queue (notification_deliveries). The worker claims rows
with FOR UPDATE SKIP LOCKED and a lease (rows stuck in SENDING after a
crash are picked up again), throttles globally and per chat, honours
Telegram's retry_after (HTTP 429), retries transient errors with bounded
backoff and resumes after restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts as t
from app.config import Settings
from app.models import Campaign, CampaignRecipient, NotificationDelivery, User
from app.services import campaigns as campaigns_svc
from app.services import events
from app.services.links import tracked_url
from app.services.search_params import format_number

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
LEASE = timedelta(minutes=5)
BATCH = 25
GLOBAL_INTERVAL = 0.05  # ≈20 messages/s, below Telegram's ~30/s bot limit
PER_CHAT_INTERVAL = 1.0
POLL_INTERVAL = 2.0

QueueModel = type[NotificationDelivery] | type[CampaignRecipient]


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


def render(delivery: NotificationDelivery, user: User, settings: Settings) -> str:
    a = delivery.auction or {}
    price = a.get("price")
    price_text = f"{format_number(price)} грн" if price not in (None, "") else "—"
    url = str(a.get("url") or "")
    link = (
        tracked_url(
            settings.public_base_url,
            settings.link_signing_key,
            user.id,
            url,
            "NOTIFICATION",
            str(a.get("number") or "") or None,
        )
        if url
        else ""
    )
    text = t.MON_NOTIFICATION.format(
        monitoring=html.escape(delivery.monitoring_name or "—"),
        title=html.escape(str(a.get("name") or "Без назви")),
        price=html.escape(price_text),
        link=html.escape(link, quote=True),
    )
    if not link:
        text = text.rsplit("\n\n", 1)[0]
    return text


class DeliveryWorker:
    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        global_interval: float = GLOBAL_INTERVAL,
        per_chat_interval: float = PER_CHAT_INTERVAL,
    ) -> None:
        self.bot = bot
        self.sf = session_factory
        self.settings = settings
        self.global_interval = global_interval
        self.per_chat_interval = per_chat_interval
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._paused_until = 0.0
        self._last_send = 0.0
        self._last_chat: dict[int, float] = {}

    # ---------- lifecycle ----------

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="delivery-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        log.info("Delivery worker started")
        while not self._stopping:
            try:
                handled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must survive DB/network hiccups
                log.exception("Delivery worker iteration failed")
                handled = 0
            if handled:
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_INTERVAL)

    # ---------- one batch ----------

    async def _claim(self, model: QueueModel) -> list[int]:
        now = datetime.now(UTC)
        async with self.sf() as session:
            due = (
                select(model.id)
                .where(
                    or_(
                        (model.status == "PENDING") & (model.next_attempt_at <= now),
                        (model.status == "SENDING") & (model.locked_at < now - LEASE),
                    )
                )
                .order_by(model.id)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
            ids = (
                await session.scalars(
                    update(model)
                    .where(model.id.in_(due.scalar_subquery()))
                    .values(status="SENDING", locked_at=now, attempts=model.attempts + 1)
                    .returning(model.id)
                )
            ).all()
            await session.commit()
        return sorted(ids)

    async def run_once(self) -> int:
        """Claim and process one batch. Auction notifications always go first;
        campaign messages are sent only while no notification is waiting.
        Returns the number of rows handled."""
        wait = self._paused_until - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        queues = (
            (NotificationDelivery, self._process),
            (CampaignRecipient, self._process_campaign),
        )
        for model, process in queues:
            ids = await self._claim(model)
            if not ids:
                continue
            for index, row_id in enumerate(ids):
                if await process(row_id):  # 429: pause, give the rest back
                    await self._release(model, ids[index + 1 :])
                    break
            if model is CampaignRecipient:
                await self._finish_campaigns(ids)
            return len(ids)
        return 0

    async def _release(self, model: QueueModel, ids: list[int]) -> None:
        """Give claimed-but-unsent rows back to the queue (after a 429)."""
        if not ids:
            return
        async with self.sf() as session:
            await session.execute(
                update(model)
                .where(model.id.in_(ids), model.status == "SENDING")
                .values(status="PENDING", locked_at=None, attempts=model.attempts - 1)
            )
            await session.commit()

    async def _finish_campaigns(self, recipient_ids: list[int]) -> None:
        async with self.sf() as session:
            campaign_ids = set(
                await session.scalars(
                    select(CampaignRecipient.campaign_id).where(
                        CampaignRecipient.id.in_(recipient_ids)
                    )
                )
            )
            for campaign_id in sorted(campaign_ids):
                await campaigns_svc.finish_if_done(session, campaign_id)
            await session.commit()

    async def _throttle(self, chat_id: int) -> None:
        now = time.monotonic()
        wait = max(
            self._last_send + self.global_interval - now,
            self._last_chat.get(chat_id, 0.0) + self.per_chat_interval - now,
            0.0,
        )
        if wait:
            await asyncio.sleep(wait)
        self._last_send = self._last_chat[chat_id] = time.monotonic()
        if len(self._last_chat) > 10_000:
            self._last_chat.clear()

    async def _process(self, delivery_id: int) -> bool:
        """Send one delivery. Returns True when sending must pause (429)."""
        async with self.sf() as session:
            d = await session.get(NotificationDelivery, delivery_id, with_for_update=True)
            if d is None or d.status != "SENDING":
                return False
            user = await session.scalar(select(User).where(User.telegram_id == d.telegram_user_id))
            now = datetime.now(UTC)
            skip_reason = None
            if user is None:
                skip_reason = "unknown user"
            elif user.anonymized_at is not None:
                skip_reason = "personal data deleted"
            elif user.access_blocked_at is not None:
                skip_reason = "access blocked by admin"
            if skip_reason:
                d.status, d.last_error, d.locked_at = "SKIPPED", skip_reason, None
                await session.commit()
                log.info("Delivery %s skipped: %s", d.id, skip_reason)
                return False
            assert user is not None

            await self._throttle(d.telegram_user_id)
            try:
                text = render(d, user, self.settings)
                msg = await self.bot.send_message(d.telegram_user_id, text)
            except TelegramRetryAfter as exc:
                d.status, d.locked_at = "PENDING", None
                d.attempts -= 1  # a flood pause is not a failed attempt
                d.next_attempt_at = now + timedelta(seconds=exc.retry_after)
                self._paused_until = time.monotonic() + exc.retry_after
                await session.commit()
                log.warning("Telegram flood limit: pausing deliveries for %ss", exc.retry_after)
                return True
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                d.status, d.locked_at, d.last_error = "FAILED", None, str(exc)[:1000]
                if isinstance(exc, TelegramForbiddenError) or "chat not found" in str(exc):
                    user.bot_blocked_at = now  # unreachable: also skipped by campaigns
                else:
                    log.error("Delivery %s rejected by Telegram: %s", d.id, exc)
                await session.commit()
                return False
            except (TelegramAPIError, OSError, TimeoutError) as exc:
                d.locked_at, d.last_error = None, f"{type(exc).__name__}: {exc}"[:1000]
                if d.attempts >= MAX_ATTEMPTS:
                    d.status = "FAILED"
                    log.error("Delivery %s failed after %s attempts: %s", d.id, d.attempts, exc)
                else:
                    d.status = "PENDING"
                    d.next_attempt_at = now + backoff(d.attempts)
                    log.warning("Delivery %s will be retried: %s", d.id, exc)
                await session.commit()
                return False

            d.status, d.locked_at, d.last_error = "SENT", None, None
            d.sent_at, d.message_id = datetime.now(UTC), msg.message_id
            if user.bot_blocked_at is not None:
                user.bot_blocked_at = None
            await events.log_event(
                session,
                events.AUCTION_NOTIFICATION_SENT,
                user_id=user.id,
                data={
                    "auction_number": d.canonical_auction_id,
                    "monitoring": d.monitoring_name,
                },
            )
            await session.commit()
            return False

    async def _process_campaign(self, recipient_id: int) -> bool:
        """Send one campaign message. Returns True when sending must pause (429)."""
        async with self.sf() as session:
            r = await session.get(CampaignRecipient, recipient_id, with_for_update=True)
            if r is None or r.status != "SENDING":
                return False
            user = await session.get(User, r.user_id)
            campaign = await session.get(Campaign, r.campaign_id)
            now = datetime.now(UTC)
            skip_reason = None
            if user is None or campaign is None or user.anonymized_at is not None:
                skip_reason = "personal data deleted"
            elif user.access_blocked_at is not None:
                skip_reason = "access blocked by admin"
            elif user.marketing_opt_out_at is not None:
                skip_reason = "opted out of marketing"  # changed after confirmation
            if skip_reason:
                r.status, r.last_error, r.locked_at = "SKIPPED", skip_reason, None
                await session.commit()
                return False
            assert user is not None and campaign is not None

            await self._throttle(user.telegram_id)
            try:
                text, markup = campaigns_svc.render(campaign, user.id, self.settings)
                msg = await self.bot.send_message(user.telegram_id, text, reply_markup=markup)
            except TelegramRetryAfter as exc:
                r.status, r.locked_at = "PENDING", None
                r.attempts -= 1  # a flood pause is not a failed attempt
                r.next_attempt_at = now + timedelta(seconds=exc.retry_after)
                self._paused_until = time.monotonic() + exc.retry_after
                await session.commit()
                log.warning("Telegram flood limit: pausing campaigns for %ss", exc.retry_after)
                return True
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                r.status, r.locked_at, r.last_error = "FAILED", None, str(exc)[:1000]
                if isinstance(exc, TelegramForbiddenError) or "chat not found" in str(exc):
                    user.bot_blocked_at = now  # unreachable for future campaigns
                else:
                    log.error("Campaign message %s rejected by Telegram: %s", r.id, exc)
                await session.commit()
                return False
            except (TelegramAPIError, OSError, TimeoutError) as exc:
                r.locked_at, r.last_error = None, f"{type(exc).__name__}: {exc}"[:1000]
                if r.attempts >= MAX_ATTEMPTS:
                    r.status = "FAILED"
                else:
                    r.status = "PENDING"
                    r.next_attempt_at = now + backoff(r.attempts)
                await session.commit()
                return False

            r.status, r.locked_at, r.last_error = "SENT", None, None
            r.sent_at, r.message_id = datetime.now(UTC), msg.message_id
            await session.commit()
            return False
