"""Status and auction-date lines for search results and notifications.

Since 22.10.2026 the API also returns lots in the editing period
(«Редагування доступне»): applications are not accepted yet. auctionStart is
null for a sale without an auction stage (simpleSell / bankRuptcy)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.bot import texts as t

KYIV = ZoneInfo("Europe/Kyiv")
_DMY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})")
MISSING = object()  # the field was not in the payload at all (e.g. demo data)


def status_line(status: Any) -> str | None:
    text = " ".join(str(status or "").split())
    if not text:
        return None
    low = text.lower()
    if "редагув" in low:
        return t.STATUS_EDITING
    if "прийняття заяв" in low or "прийом заяв" in low:
        return t.STATUS_ACCEPTING
    return t.STATUS_OTHER.format(status=text)


def _date(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(ts, KYIV).strftime("%d.%m.%Y")
    text = str(value).strip()
    m = _DMY.match(text)
    if m:
        return f"{int(m[1]):02d}.{int(m[2]):02d}.{m[3]}"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:40] or None
    if dt.tzinfo is not None:
        dt = dt.astimezone(KYIV)
    return dt.strftime("%d.%m.%Y")


def start_line(auction_start: Any) -> str | None:
    """null → sale without an auction; field absent → nothing is shown."""
    if auction_start is MISSING:
        return None
    if auction_start in (None, ""):
        return t.NO_AUCTION_STAGE
    day = _date(auction_start)
    return t.AUCTION_DATE.format(date=day) if day else None


def lines(status: Any, auction_start: Any) -> list[str]:
    return [x for x in (status_line(status), start_line(auction_start)) if x]


def from_payload(a: dict) -> list[str]:
    """For a webhook auction dict (stored on the delivery)."""
    return lines(a.get("status"), a["auctionStart"] if "auctionStart" in a else MISSING)
