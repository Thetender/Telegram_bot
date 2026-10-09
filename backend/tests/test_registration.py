from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from sqlalchemy import func, select

from app.bot import texts as t
from app.models import Event, User, UserLegalAcceptance
from app.services import legal
from tests.fake_telegram import contact_update, message_update

UID = 1001


async def seed_legal(sf) -> None:
    async with sf() as s:
        for doc in (legal.TERMS, legal.PRIVACY):
            await legal.publish_version(
                s,
                doc_type=doc,
                version="1.0",
                public_url=f"https://example.com/{doc.lower()}",
                stored_reference=f"{doc.lower()}-1.0.pdf",
                effective_at=datetime.now(UTC),
            )
        await s.commit()


async def test_first_start_shows_legal_notice_and_share_phone(harness):
    await seed_legal(harness.sf)
    await harness.feed(message_update(UID, "/start"))

    sent = harness.tg.sent()
    assert sent[0].text == t.WELCOME
    links = sent[0].reply_markup
    assert isinstance(links, InlineKeyboardMarkup)
    urls = [row[0].url for row in links.inline_keyboard]
    assert urls == ["https://example.com/terms", "https://example.com/privacy"]

    share = sent[1].reply_markup
    assert isinstance(share, ReplyKeyboardMarkup)
    button = share.keyboard[0][0]
    assert button.text == t.BTN_SHARE_PHONE and button.request_contact is True


async def test_own_contact_registers_user(harness, db):
    await seed_legal(harness.sf)
    await harness.feed(contact_update(UID, contact_user_id=UID, phone="380671234567"))

    user = await db.scalar(select(User).where(User.telegram_id == UID))
    assert user is not None
    assert user.phone == "+380671234567"
    assert user.name == "Іван Петренко"
    assert user.username == "ivan"

    texts = harness.tg.texts()
    assert texts == [t.REGISTRATION_DONE, t.MAIN_MENU]
    menu = harness.tg.sent()[-1].reply_markup
    labels = [b.text for row in menu.keyboard for b in row]
    assert labels == [t.BTN_SEARCH, t.BTN_MONITORINGS, t.BTN_CONSULTATION, t.BTN_HELP]

    accepted = await db.scalar(
        select(func.count()).select_from(UserLegalAcceptance).where(
            UserLegalAcceptance.user_id == user.id
        )
    )
    assert accepted == 2
    registered_events = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "USER_REGISTERED")
    )
    assert registered_events == 1


async def test_foreign_contact_is_rejected(harness, db):
    await harness.feed(contact_update(UID, contact_user_id=999999))
    assert await db.scalar(select(func.count()).select_from(User)) == 0
    assert harness.tg.texts() == [t.FOREIGN_CONTACT]


async def test_contact_without_user_id_is_rejected(harness, db):
    # A contact typed manually / forwarded card has no user_id.
    await harness.feed(contact_update(UID, contact_user_id=None))
    assert await db.scalar(select(func.count()).select_from(User)) == 0
    assert harness.tg.texts() == [t.FOREIGN_CONTACT]


async def test_registered_user_start_opens_main_menu(harness):
    await harness.feed(contact_update(UID, contact_user_id=UID))
    harness.tg.clear()
    await harness.feed(message_update(UID, "/start"))
    assert harness.tg.texts() == [t.MAIN_MENU]


async def test_repeated_contact_does_not_duplicate_user(harness, db):
    await harness.feed(contact_update(UID, contact_user_id=UID, phone="380671234567"))
    await harness.feed(contact_update(UID, contact_user_id=UID, phone="380501112233"))
    users = (await db.scalars(select(User))).all()
    assert len(users) == 1
    assert users[0].phone == "+380501112233"


async def test_unregistered_user_is_asked_to_register(harness):
    await harness.feed(message_update(UID, "привіт"))
    assert harness.tg.texts() == [t.REGISTRATION_REQUIRED]
    harness.tg.clear()
    await harness.feed(message_update(UID, t.BTN_SEARCH))
    assert harness.tg.texts() == [t.REGISTRATION_REQUIRED]
    harness.tg.clear()
    await harness.feed(message_update(UID, "/menu"))
    assert harness.tg.texts() == [t.REGISTRATION_REQUIRED]


async def test_duplicate_update_is_processed_once(harness, db):
    update = contact_update(UID, contact_user_id=UID)
    await harness.feed(update)
    harness.tg.clear()
    await harness.feed(update)  # Telegram re-delivers the same update_id
    assert harness.tg.texts() == []
    count = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "USER_REGISTERED")
    )
    assert count == 1
