from __future__ import annotations

from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import AnswerCallbackQuery
from sqlalchemy import func, select

from app.bot import keyboards as kb
from app.bot import texts as t
from app.models import ROLE_MANAGER, Event, PrivacyRequest, User
from app.services import users as users_svc
from tests.fake_telegram import BOT_ID, callback_update, contact_update, message_update

UID = 2002


async def register(harness) -> None:
    await harness.feed(contact_update(UID, contact_user_id=UID))
    harness.tg.clear()


def callback_answers(harness) -> list[AnswerCallbackQuery]:
    return [r for r in harness.tg.requests if isinstance(r, AnswerCallbackQuery)]


async def test_menu_command(harness):
    await register(harness)
    await harness.feed(message_update(UID, "/menu"))
    assert harness.tg.texts() == [t.MAIN_MENU]


async def test_menu_buttons_show_placeholders(harness):
    await register(harness)
    for button, section in [
        (t.BTN_MONITORINGS, "Мої моніторинги"),
    ]:
        harness.tg.clear()
        await harness.feed(message_update(UID, button))
        assert harness.tg.texts() == [t.coming_soon(section)]


async def test_menu_text_overrides_pending_input(harness):
    """Menu buttons work in any state and clear pending text input."""
    await register(harness)
    key = StorageKey(bot_id=BOT_ID, chat_id=UID, user_id=UID)
    await harness.dp.storage.set_state(key, "SomeForm:waiting_keyword")
    await harness.feed(message_update(UID, t.BTN_HELP))
    assert harness.tg.texts() == [t.HELP_TITLE]
    assert await harness.dp.storage.get_state(key) is None


async def test_my_requests_only_for_managers(harness, db):
    await register(harness)
    await harness.feed(message_update(UID, t.BTN_MY_REQUESTS))
    assert harness.tg.texts() == [t.UNKNOWN_INPUT]

    user = await db.scalar(select(User).where(User.telegram_id == UID))
    await users_svc.grant_role(db, user, ROLE_MANAGER, actor_user_id=None)
    await db.commit()

    harness.tg.clear()
    await harness.feed(message_update(UID, "/menu"))
    labels = [b.text for row in harness.tg.sent()[-1].reply_markup.keyboard for b in row]
    assert t.BTN_MY_REQUESTS in labels
    harness.tg.clear()
    await harness.feed(message_update(UID, t.BTN_MY_REQUESTS))
    assert harness.tg.texts()[-1].startswith(t.MY_REQUESTS_TITLE)


async def test_help_sections(harness):
    await register(harness)
    await harness.feed(message_update(UID, t.BTN_HELP))
    markup = harness.tg.sent()[-1].reply_markup
    labels = [row[0].text for row in markup.inline_keyboard]
    assert labels == [
        t.BTN_FAQ,
        t.BTN_ABOUT,
        t.BTN_TARIFFS,
        t.BTN_HELP_CONSULTATION,
        t.BTN_NOTIFICATION_SETTINGS,
        t.BTN_DELETE_DATA,
    ]  # legal links appear once legal documents are published

    sections = [("faq", t.HELP_FAQ), ("about", t.HELP_ABOUT), ("tariffs", t.HELP_TARIFFS)]
    for section, expected in sections:
        harness.tg.clear()
        await harness.feed(callback_update(UID, kb.HelpCb(section=section).pack()))
        assert harness.tg.texts() == [expected]
        assert len(callback_answers(harness)) == 1  # callback always answered


async def test_marketing_opt_out_toggle(harness, db):
    await register(harness)
    await harness.feed(callback_update(UID, kb.HelpCb(section="settings").pack()))
    assert harness.tg.texts() == [t.marketing_state(True)]

    harness.tg.clear()
    await harness.feed(callback_update(UID, kb.MarketingCb(enable=False).pack()))
    assert harness.tg.texts() == [t.marketing_state(False)]
    assert callback_answers(harness)[0].text == t.SETTINGS_SAVED
    user = await db.scalar(select(User).where(User.telegram_id == UID))
    assert user.marketing_opt_out_at is not None

    # Double tap does not create a second event
    await harness.feed(callback_update(UID, kb.MarketingCb(enable=False).pack()))
    events = await db.scalar(
        select(func.count()).select_from(Event).where(
            Event.event_type == "MARKETING_PREFERENCE_CHANGED"
        )
    )
    assert events == 1

    await harness.feed(callback_update(UID, kb.MarketingCb(enable=True).pack()))
    await db.refresh(user)
    assert user.marketing_opt_out_at is None


async def test_personal_data_deletion_request_created_once(harness, db):
    await register(harness)
    await harness.feed(callback_update(UID, kb.HelpCb(section="delete").pack()))
    assert harness.tg.texts() == [t.DELETE_DATA_EXPLANATION]

    harness.tg.clear()
    await harness.feed(callback_update(UID, kb.PrivacyCb(action="send").pack()))
    assert harness.tg.texts() == [t.DELETE_REQUEST_CREATED]
    harness.tg.clear()
    await harness.feed(callback_update(UID, kb.PrivacyCb(action="send").pack()))
    assert harness.tg.texts() == [t.DELETE_REQUEST_EXISTS]
    assert await db.scalar(select(func.count()).select_from(PrivacyRequest)) == 1


async def test_unknown_callback_is_answered_gracefully(harness):
    await register(harness)
    await harness.feed(callback_update(UID, "zz:unknown"))
    answers = callback_answers(harness)
    assert len(answers) == 1 and answers[0].text == t.STALE_ACTION


async def test_unknown_text_shows_menu(harness):
    await register(harness)
    await harness.feed(message_update(UID, "щось незрозуміле"))
    assert harness.tg.texts() == [t.UNKNOWN_INPUT]


def test_main_menu_keyboard_folds_away_after_tap():
    from app.bot import keyboards as kb

    markup = kb.main_menu(set())
    assert markup.one_time_keyboard is True and not markup.is_persistent


def test_text_constants_are_not_redefined():
    """A later constant with the same name silently changes an earlier button."""
    import ast
    import pathlib

    import app.bot.texts as texts_module

    tree = ast.parse(pathlib.Path(texts_module.__file__).read_text())
    names = [
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]
    assert len(names) == len(set(names)), sorted({n for n in names if names.count(n) > 1})
