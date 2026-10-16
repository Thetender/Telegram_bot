"""New-auction notifications from The Tender (FR §7.1, Architecture §8).

Webhook pattern: authenticate → parse → persist one delivery row per
(auction, Telegram user) with ON CONFLICT DO NOTHING → answer 200. The
delivery worker sends the Telegram messages later. Webhook retries from
The Tender (every 300 s until 2xx) are therefore always safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Monitoring, MonitoringWatcher, NotificationDelivery, User

log = logging.getLogger(__name__)

AUCTION_FIELDS = (
    "name",
    "number",
    "companyName",
    "auctionStart",
    "status",
    "start_price_type",
    "auction_type",
    "price",
    "url",
    "balance_holder",
)


class WebhookError(ValueError):
    """Payload cannot be processed (permanent: retrying will not help)."""


@dataclass
class ParsedEvent:
    canonical_auction_id: str
    auction: dict[str, Any]
    recipients: list[tuple[int, str | None]]  # (telegram user id, monitoring name)


def _recipients(users: Any) -> list[tuple[int, str | None]]:
    """users: [{"<user_id>": "<monitoring name>"}, ...] (API doc §7).
    Also tolerates a single mapping and {"user_id": .., "name": ..} items."""
    items: list[tuple[Any, Any]] = []
    if isinstance(users, dict):
        items = list(users.items())
    elif isinstance(users, list):
        for entry in users:
            if isinstance(entry, dict) and "user_id" in entry:
                items.append((entry.get("user_id"), entry.get("name")))
            elif isinstance(entry, dict):
                items.extend(entry.items())
            elif isinstance(entry, (str, int)):
                items.append((entry, None))
    out: dict[int, str | None] = {}
    for raw_id, name in items:
        try:
            tg_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            log.warning("Webhook: skipping invalid user id %r", raw_id)
            continue
        if tg_id <= 0:
            continue
        out.setdefault(tg_id, str(name).strip()[:255] if name not in (None, "") else None)
    return list(out.items())


def parse_event(payload: Any) -> ParsedEvent | None:
    """None for events this bot does not handle (answered 200, ignored)."""
    if not isinstance(payload, dict):
        raise WebhookError("payload is not an object")
    if payload.get("event_type") != "new_auction":
        return None
    auction = payload.get("auction_data")
    if not isinstance(auction, dict):
        raise WebhookError("auction_data missing")
    # Canonical auction id: auction_data.number (to be confirmed unique and
    # immutable by The Tender before production); the URL is a fallback.
    canonical = str(auction.get("number") or "").strip() or str(auction.get("url") or "").strip()
    if not canonical:
        raise WebhookError("auction has neither number nor url")
    kept = {k: auction.get(k) for k in AUCTION_FIELDS if k in auction}
    return ParsedEvent(canonical[:128], kept, _recipients(payload.get("users")))


async def ingest(session: AsyncSession, event: ParsedEvent) -> int:
    """Persist deliveries; returns how many new rows were queued."""
    if not event.recipients:
        return 0
    rows = [
        {
            "canonical_auction_id": event.canonical_auction_id,
            "telegram_user_id": tg_id,
            "monitoring_name": name,
            "for_user_id": 0,
            "auction": event.auction,
        }
        for tg_id, name in event.recipients
    ]
    rows += await _watcher_copies(session, event)
    result = await session.execute(
        insert(NotificationDelivery)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_delivery_auction_user_for")
        .returning(NotificationDelivery.id)
    )
    return len(result.all())


async def _watcher_copies(session: AsyncSession, event: ParsedEvent) -> list[dict[str, Any]]:
    """Copies for managers/admins responsible for the matched monitorings.
    The webhook names the monitoring, so it is matched by (client, name)."""
    wanted = {(tg_id, name) for tg_id, name in event.recipients if name}
    if not wanted:
        return []
    client, watcher = aliased(User), aliased(User)
    rows = await session.execute(
        select(client.telegram_id, client.id, Monitoring.name, watcher.telegram_id)
        .join(Monitoring, Monitoring.user_id == client.id)
        .join(MonitoringWatcher, MonitoringWatcher.monitoring_id == Monitoring.id)
        .join(watcher, watcher.id == MonitoringWatcher.user_id)
        .where(
            client.telegram_id.in_({tg for tg, _ in wanted}),
            Monitoring.deleted_at.is_(None),
        )
    )
    out = []
    for client_tg, client_id, mon_name, watcher_tg in rows:
        if (client_tg, mon_name) in wanted:
            out.append(
                {
                    "canonical_auction_id": event.canonical_auction_id,
                    "telegram_user_id": watcher_tg,
                    "monitoring_name": mon_name,
                    "for_user_id": client_id,
                    "auction": event.auction,
                }
            )
    return out
