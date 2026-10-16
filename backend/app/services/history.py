"""User activity history for the dashboard: readable rows with details."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Monitoring, SearchSnapshot, User
from app.services import events
from app.services.search_params import summary_lines

# Filter tabs above the history → event types.
KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "search": ("Пошуки", (events.SEARCH_COMPLETED,)),
    "monitor": (
        "Моніторинги",
        (events.MONITOR_CREATED, events.MONITOR_UPDATED, events.MONITOR_DELETED),
    ),
    "auction": ("Аукціони", (events.AUCTION_OPENED,)),
    "consult": (
        "Консультації",
        (
            events.CONSULTATION_REQUESTED,
            events.CONSULTATION_TAKEN,
            events.CONSULTATION_COMPLETED,
            events.CONSULTATION_CLOSED_BY_ADMIN,
            events.CONSULTATION_RESULT_CORRECTED,
        ),
    ),
}
# Not shown: sent by the system to the user, or noise before every search.
HIDDEN = (events.AUCTION_NOTIFICATION_SENT, events.SEARCH_STARTED)

OPENED_FROM = {"SEARCH": "з пошуку", "NOTIFICATION": "зі сповіщення", "CAMPAIGN": "з розсилки"}
ROLE_NAMES = {"ADMIN": "адмін", "MANAGER": "менеджер"}


@dataclass
class HistoryRow:
    created_at: Any
    label: str
    details: list[str] = field(default_factory=list)  # plain text, escaped by the template
    link: tuple[str, str] | None = None  # (text, url)
    changes: list[list[str]] = field(default_factory=list)  # [field, before, after]
    count: int = 1

    def same_as(self, other: HistoryRow) -> bool:
        return (self.label, self.details, self.link, self.changes) == (
            other.label,
            other.details,
            other.link,
            other.changes,
        )


def filters_text(params: dict[str, Any] | None) -> str:
    return " · ".join(value for _, value in summary_lines(params)) or "без фільтрів"


def _without_utm(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


async def build(
    session: AsyncSession,
    timeline: list[Event],
    labels: dict[str, str],
    actors: dict[int, User],
    profile_user_id: int,
) -> list[HistoryRow]:
    # Names of monitorings (deleted ones too) and parameters of old searches.
    mon_ids = {e.event_data.get("monitoring_id") for e in timeline} - {None}
    names = {}
    if mon_ids:
        names = {
            m.id: m.name
            for m in await session.scalars(select(Monitoring).where(Monitoring.id.in_(mon_ids)))
        }
    snap_ids = {
        e.event_data.get("snapshot_id") for e in timeline if "params" not in e.event_data
    } - {None}
    snaps = {}
    if snap_ids:
        snaps = {
            s.id: s.params
            for s in await session.scalars(
                select(SearchSnapshot).where(SearchSnapshot.id.in_(snap_ids))
            )
        }

    rows: list[HistoryRow] = []
    for e in timeline:
        d = e.event_data or {}
        row = HistoryRow(e.created_at, labels.get(e.event_type, e.event_type))
        t = e.event_type
        if t == events.SEARCH_COMPLETED:
            params = d.get("params", snaps.get(d.get("snapshot_id")))
            if params is not None:
                row.details.append(filters_text(params))
            if d.get("items_count") is not None:
                row.details.append(f"знайдено {d['items_count']}")
        elif t == events.AUCTION_OPENED:
            number = d.get("auction_number") or ""
            if number.startswith("campaign:"):
                row.details.append(f"розсилка №{number.split(':', 1)[1]}")
                number = ""
            if d.get("url"):
                row.link = (number or "посилання", _without_utm(d["url"]))
            elif number:
                row.details.append(number)
            if d.get("source") in OPENED_FROM:
                row.details.append(OPENED_FROM[d["source"]])
        elif t in (events.MONITOR_CREATED, events.MONITOR_UPDATED, events.MONITOR_DELETED):
            name = d.get("name") or names.get(d.get("monitoring_id"))
            if name:
                row.details.append(f"«{name}»")
            if t == events.MONITOR_CREATED and "params" in d:
                row.details.append(filters_text(d["params"]))
            row.changes = d.get("changes") or []
        elif t in KINDS["consult"][1] and d.get("request_id"):
            row.link = (f"заявка №{d['request_id']}", f"/admin/consultations/{d['request_id']}")
        elif t in (events.FEEDBACK_CREATED, events.FEEDBACK_REPLIED) and d.get("feedback_id"):
            row.link = (f"звернення №{d['feedback_id']}", f"/admin/feedback/{d['feedback_id']}")
        elif t == events.BOT_STARTED and d.get("source"):
            row.details.append(f"джерело: {d['source']}")
        elif t == events.MARKETING_PREFERENCE_CHANGED and "marketing_enabled" in d:
            row.label = "Увімкнув розсилки" if d["marketing_enabled"] else "Вимкнув розсилки"
        elif t in (events.ROLE_GRANTED, events.ROLE_REVOKED) and d.get("role"):
            row.details.append(ROLE_NAMES.get(d["role"], d["role"]))
        if e.actor_user_id and e.actor_user_id != profile_user_id:
            actor = actors.get(e.actor_user_id)
            row.details.append(actor.name if actor and actor.name else "адміністратор")
        # Repeated identical actions in a row collapse into one line «×N».
        if rows and rows[-1].same_as(row):
            rows[-1].count += 1
        else:
            rows.append(row)
    return rows
