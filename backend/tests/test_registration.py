from __future__ import annotations

from datetime import UTC, datetime

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


async def test_first_start_is_one_welcome_with_begin(harness, db):
    from app.bot import keyboards as kb
    from tests.fake_telegram import callback_update

    await seed_legal(harness.sf)
    await harness.feed(message_update(UID, "/start"))

    (welcome,) = harness.tg.sent()
    assert welcome.text.startswith("👋 Вітаємо в THE TENDER!")
    assert '<a href="https://example.com/terms">Правила користування</a>' in welcome.text
    (row,) = welcome.reply_markup.inline_keyboard
    assert row[0].text == t.BTN_BEGIN
    assert await db.scalar(select(func.count()).select_from(User)) == 0  # nothing yet

    harness.tg.clear()
    await harness.feed(callback_update(UID, kb.BeginCb().pack()))
    user = await db.scalar(select(User).where(User.telegram_id == UID))
    assert user is not None and user.phone is None  # no phone at the start
    assert harness.tg.texts()[-1] == t.MAIN_MENU
    menu = harness.tg.sent()[-1].reply_markup
    labels = [b.text for row in menu.keyboard for b in row]
    assert labels == [t.BTN_SEARCH, t.BTN_MONITORINGS, t.BTN_CONSULTATION, t.BTN_HELP]
    accepted = await db.scalar(
        select(func.count())
        .select_from(UserLegalAcceptance)
        .where(UserLegalAcceptance.user_id == user.id)
    )
    assert accepted == 1  # the one active document (Правила)
    registered = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "USER_REGISTERED")
    )
    assert registered == 1


async def test_own_contact_saves_phone(harness, db):
    await seed_legal(harness.sf)
    await harness.feed(contact_update(UID, contact_user_id=UID, phone="380671234567"))

    user = await db.scalar(select(User).where(User.telegram_id == UID))
    assert user is not None
    assert user.phone == "+380671234567"
    assert user.name == "Іван Петренко"
    assert user.username == "ivan"
    assert harness.tg.texts() == [t.PHONE_SAVED]


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


async def test_unknown_visitor_gets_the_welcome(harness):
    for text in ("привіт", t.BTN_SEARCH, "/menu"):
        harness.tg.clear()
        await harness.feed(message_update(UID, text))
        assert harness.tg.texts()[0].startswith("👋 Вітаємо в THE TENDER!")


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


async def test_phone_is_asked_for_the_first_monitoring_and_consultation(harness, db):
    from app.bot import keyboards as kb
    from app.bot.handlers.consultation import CCb
    from app.bot.handlers.results import RCb
    from app.bot.handlers.search import SCb
    from app.models import Monitoring, SearchSnapshot
    from tests.fake_telegram import callback_update

    h = harness
    await h.feed(message_update(UID, "/start"))
    await h.feed(callback_update(UID, kb.BeginCb().pack()))
    await h.feed(callback_update(UID, SCb(a="set", v="auction_type=sale").pack()))
    await h.feed(callback_update(UID, SCb(a="run").pack()))
    snap = await db.scalar(select(SearchSnapshot))

    # Monitoring → phone first, then straight to the naming step.
    h.tg.clear()
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    assert h.tg.texts() == [t.PHONE_FOR_MONITORING]
    share = h.tg.sent()[-1].reply_markup
    assert share.keyboard[0][0].request_contact and share.keyboard[1][0].text == t.BTN_CANCEL_PHONE
    h.tg.clear()
    await h.feed(contact_update(UID, contact_user_id=UID, phone="380671234567"))
    assert h.tg.texts() == [t.PHONE_SAVED, t.MON_ASK_NAME]
    await h.feed(message_update(UID, "Склади"))
    assert (await db.scalar(select(func.count()).select_from(Monitoring))) == 1

    # With a phone already known nothing is asked again.
    await h.feed(callback_update(UID, CCb(a="open").pack()))
    assert t.PHONE_FOR_CONSULTATION not in h.tg.texts()


async def test_consultation_asks_for_phone_and_cancel_returns_to_menu(harness, db):
    from app.bot import keyboards as kb
    from tests.fake_telegram import callback_update

    h = harness
    await h.feed(message_update(UID, "/start"))
    await h.feed(callback_update(UID, kb.BeginCb().pack()))
    h.tg.clear()
    await h.feed(message_update(UID, t.BTN_CONSULTATION))
    assert h.tg.texts() == [t.PHONE_FOR_CONSULTATION]
    await h.feed(message_update(UID, t.BTN_CANCEL_PHONE))
    assert h.tg.texts()[-1] == t.MAIN_MENU
    h.tg.clear()
    await h.feed(message_update(UID, t.BTN_CONSULTATION))
    await h.feed(contact_update(UID, contact_user_id=UID, phone="380671234567"))
    assert h.tg.texts()[1] == t.PHONE_SAVED
    assert t.BTN_ORDER_CONSULTATION in str(h.tg.sent()[-1].reply_markup)
