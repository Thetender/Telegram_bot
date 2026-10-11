"""Help texts editable in the Dashboard (Налаштування → Тексти бота)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
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


TEXT, LINK, DOC = "text", "link", "doc"
MODES = (TEXT, LINK, DOC)
MAX_CAPTION = 1000  # Telegram caption limit is 1024
MAX_FILE = 20 * 1024 * 1024
DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt")
DEFAULT_BUTTON = "🔗 Детальніше"


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    mode: str  # text | link | doc
    text: str  # the text, or the description for link/doc (may be empty)
    is_default: bool
    button_text: str | None = None
    button_url: str | None = None
    file_name: str | None = None
    file_id: str | None = None


async def get_section(session: AsyncSession, key: str) -> Section:
    title, default = EDITABLE[key]
    row = await session.get(BotText, key)
    if row is None:
        return Section(key, title, TEXT, default, True)
    return Section(
        key,
        title,
        row.mode if row.mode in MODES else TEXT,
        row.value,
        False,
        row.button_text,
        row.button_url,
        row.file_name,
        row.file_id,
    )


async def all_sections(session: AsyncSession) -> list[Section]:
    return [await get_section(session, key) for key in EDITABLE]


async def file_content(session: AsyncSession, key: str) -> bytes | None:
    return await session.scalar(select(BotText.file_content).where(BotText.key == key))


async def remember_file_id(session: AsyncSession, key: str, file_id: str) -> None:
    row = await session.get(BotText, key)
    if row is not None:
        row.file_id = file_id[:255]


async def save_section(
    session: AsyncSession,
    key: str,
    admin: User,
    mode: str,
    value: str,
    button_text: str = "",
    button_url: str = "",
    file_name: str | None = None,
    content: bytes | None = None,
) -> list[str]:
    """Text OR link with a description OR document. Returns errors (nothing saved)."""
    if key not in EDITABLE:
        raise KeyError(key)
    if mode not in MODES:
        return ["Невідомий тип розділу."]
    value = (value or "").replace("\r\n", "\n").strip()
    row = await session.get(BotText, key)
    values: dict = {"mode": mode, "value": value, "updated_by": admin.id}
    if mode == TEXT:
        errors = validate(value, key)
    elif mode == LINK:
        url = (button_url or "").strip()
        errors = telegram_html_errors(value, MAX_LEN) if value else []
        if not url.startswith("https://") or len(url) > 1024 or " " in url:
            errors.append("Вкажіть посилання, що починається з https://")
        label = " ".join((button_text or "").split()) or DEFAULT_BUTTON
        if len(label) > 64:
            errors.append("Назва кнопки — до 64 символів.")
        values |= {"button_text": label, "button_url": url}
    else:
        errors = telegram_html_errors(value, MAX_CAPTION) if value else []
        if content is not None:
            name = (file_name or "document").strip()[:255]
            if len(content) > MAX_FILE:
                errors.append("Файл більший за 20 МБ.")
            elif not name.lower().endswith(DOC_EXTENSIONS):
                errors.append("Дозволені файли: PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT.")
            values |= {"file_name": name, "file_content": content, "file_id": None}
        elif row is None or not row.file_name:
            errors.append("Завантажте файл документа.")
    if errors:
        return errors
    await session.execute(
        insert(BotText)
        .values(key=key, **values)
        .on_conflict_do_update(index_elements=[BotText.key], set_=values)
    )
    await events.log_event(
        session,
        events.SETTINGS_CHANGED,
        actor_user_id=admin.id,
        data={"bot_text": key, "mode": mode},
    )
    return []


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
    return await save_section(session, key, admin, TEXT, value)


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
