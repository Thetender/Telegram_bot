"""Help texts editable in the Dashboard (Налаштування → Тексти бота)."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts as t
from app.models import BotText, User
from app.services import events
from app.services.campaigns import telegram_html_errors

MAX_LEN = 3500

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


def validate(value: str) -> list[str]:
    value = (value or "").strip()
    if not value:
        return ["Текст не може бути порожнім."]
    return telegram_html_errors(value, MAX_LEN)


async def save(session: AsyncSession, key: str, value: str, admin: User) -> list[str]:
    if key not in EDITABLE:
        raise KeyError(key)
    value = (value or "").replace("\r\n", "\n").strip()
    errors = validate(value)
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
