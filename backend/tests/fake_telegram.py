"""In-memory fake of the Telegram Bot API for tests.

Records every outgoing API call; no network access.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, GetMe, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update
from aiogram.types import User as TgUser

BOT_ID = 42


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self._ids = itertools.count(1000)
        # Chats that "blocked the bot": sending there raises TelegramForbiddenError.
        self.fail_chats: set[int] = set()
        # Exceptions raised by the next SendMessage calls, in order (e.g. 429).
        self.raise_next: list[Exception] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None):
        chat = getattr(method, "chat_id", None)
        if chat is not None and int(chat) in self.fail_chats:
            raise TelegramForbiddenError(
                method=method, message="Forbidden: bot was blocked by the user"
            )
        if isinstance(method, SendMessage) and self.raise_next:
            raise self.raise_next.pop(0)
        self.requests.append(method)
        if isinstance(method, GetMe):
            return TgUser(id=BOT_ID, is_bot=True, first_name="Test bot", username="TgtestTT_bot")
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = method.chat_id if method.chat_id is not None else 0
            return Message(
                message_id=method.message_id
                if isinstance(method, EditMessageText) and method.message_id
                else next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=int(chat_id), type="private"),
                text=method.text,
            )
        return True

    async def close(self) -> None:  # pragma: no cover
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError  # pragma: no cover
        yield b""

    # --- helpers for assertions ---
    def sent(self) -> list[SendMessage | EditMessageText]:
        return [r for r in self.requests if isinstance(r, (SendMessage, EditMessageText))]

    def texts(self) -> list[str]:
        return [r.text for r in self.sent()]

    def to(self, chat_id: int) -> list[SendMessage | EditMessageText]:
        return [r for r in self.sent() if int(r.chat_id or 0) == chat_id]

    def clear(self) -> None:
        self.requests.clear()


def make_bot() -> tuple[Bot, FakeSession]:
    session = FakeSession()
    bot = Bot(token=f"{BOT_ID}:TEST_TOKEN_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", session=session)
    return bot, session


_update_ids = itertools.count(1)


def tg_user(uid: int, username: str | None = None) -> TgUser:
    return TgUser(id=uid, is_bot=False, first_name="Іван", last_name="Петренко", username=username)


def message_update(
    uid: int,
    text: str | None = None,
    contact: Contact | None = None,
    update_id: int | None = None,
    username: str | None = "ivan",
) -> Update:
    msg = Message(
        message_id=next(_update_ids),
        date=datetime.now(UTC),
        chat=Chat(id=uid, type="private"),
        from_user=tg_user(uid, username),
        text=text,
        contact=contact,
    )
    return Update(update_id=update_id or next(_update_ids), message=msg)


def contact_update(uid: int, contact_user_id: int | None, phone: str = "380671234567") -> Update:
    contact = Contact(phone_number=phone, first_name="Іван", user_id=contact_user_id)
    return message_update(uid, contact=contact)


def callback_update(uid: int, data: str, message_id: int = 555) -> Update:
    msg = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=uid, type="private"),
        from_user=TgUser(id=BOT_ID, is_bot=True, first_name="bot"),
        text="old",
    )
    cq = CallbackQuery(
        id=str(next(_update_ids)),
        from_user=tg_user(uid),
        chat_instance="ci",
        message=msg,
        data=data,
    )
    return Update(update_id=next(_update_ids), callback_query=cq)
