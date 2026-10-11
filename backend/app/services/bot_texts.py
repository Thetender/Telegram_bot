"""Help texts editable in the Dashboard (Налаштування → Тексти бота)."""

from __future__ import annotations

import re

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts as t
from app.models import BotText, User
from app.services import events
from app.services.campaigns import telegram_html_errors

MAX_LEN = 3500  # one Telegram message
MAX_FAQ_LEN = 30000  # FAQ is shown one answer at a time
MAX_FAQ_ITEMS = 60
_QUESTION = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+?)\s*$")

# key → (title in the Dashboard, built-in default)
EDITABLE: dict[str, tuple[str, str]] = {
    "HELP_FAQ": ("❓ FAQ", t.HELP_FAQ),
    "HELP_ABOUT": ("ℹ️ Про Prozorro.Продажі", t.HELP_ABOUT),
    "HELP_TARIFFS": ("💳 Тарифи", t.HELP_TARIFFS),
}


async def get(session: AsyncSession, key: str) -> str:
    row = await session.get(BotText, key)
    return row.value if row is not None else EDITABLE[key][1]


async def all_texts(session: AsyncSession) -> dict[str, tuple[str, str, bool]]:
    """key → (title, current value, is_default)."""
    out = {}
    for key, (title, default) in EDITABLE.items():
        row = await session.get(BotText, key)
        out[key] = (title, row.value if row else default, row is None)
    return out


def parse_faq(text: str) -> list[tuple[str, str]]:
    """«1. Питання?» lines start a new question; the following lines are its answer."""
    items: list[tuple[str, list[str]]] = []
    for line in (text or "").splitlines():
        m = _QUESTION.match(line)
        if m:
            items.append((m.group(2), []))
        elif items:
            items[-1][1].append(line)
    return [(q, "\n".join(a).strip()) for q, a in items]


def validate(value: str, key: str = "") -> list[str]:
    value = (value or "").strip()
    if not value:
        return ["Текст не може бути порожнім."]
    if key == "HELP_FAQ":
        items = parse_faq(value)
        if items:
            errors = telegram_html_errors(value, MAX_FAQ_LEN)
            if len(items) > MAX_FAQ_ITEMS:
                errors.append(f"Забагато питань: {len(items)} (максимум {MAX_FAQ_ITEMS}).")
            for n, (_q, a) in enumerate(items, 1):
                if not a:
                    errors.append(f"Питання {n}: немає відповіді.")
                errors += [f"Питання {n}: {e}" for e in telegram_html_errors(a, MAX_LEN)]
            return errors
    return telegram_html_errors(value, MAX_LEN)


async def save(session: AsyncSession, key: str, value: str, admin: User) -> list[str]:
    if key not in EDITABLE:
        raise KeyError(key)
    value = (value or "").replace("\r\n", "\n").strip()
    errors = validate(value, key)
    if errors:
        return errors
    await session.execute(
        insert(BotText)
        .values(key=key, value=value, updated_by=admin.id)
        .on_conflict_do_update(
            index_elements=[BotText.key], set_={"value": value, "updated_by": admin.id}
        )
    )
    await events.log_event(
        session, events.SETTINGS_CHANGED, actor_user_id=admin.id, data={"bot_text": key}
    )
    return []


async def reset(session: AsyncSession, key: str, admin: User) -> None:
    row = await session.get(BotText, key)
    if row is not None:
        await session.delete(row)
        await events.log_event(
            session,
            events.SETTINGS_CHANGED,
            actor_user_id=admin.id,
            data={"bot_text": key, "reset": True},
        )
