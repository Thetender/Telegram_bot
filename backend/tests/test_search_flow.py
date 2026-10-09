from __future__ import annotations

from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select

from app.bot import texts as t
from app.bot.handlers.results import RCb
from app.bot.handlers.search import SCb, category_key
from app.models import Event, SearchDraft, SearchSnapshot, User
from app.services.search_params import REGIONS
from tests.fake_telegram import callback_update, contact_update, message_update

UID = 4004


async def register(h) -> None:
    await h.feed(contact_update(UID, contact_user_id=UID))
    h.tg.clear()


async def tap(h, a: str, v: str = "") -> None:
    await h.feed(callback_update(UID, SCb(a=a, v=v).pack()))


async def say(h, text: str) -> None:
    await h.feed(message_update(UID, text))


async def draft(h) -> dict:
    async with h.sf() as s:
        user = await s.scalar(select(User).where(User.telegram_id == UID))
        d = await s.get(SearchDraft, user.id)
        return dict(d.params) if d else {}


def answers(h) -> list[AnswerCallbackQuery]:
    return [r for r in h.tg.requests if isinstance(r, AnswerCallbackQuery)]


def buttons(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data or b.url) for row in markup.inline_keyboard for b in row]


def last_markup(h) -> InlineKeyboardMarkup:
    return h.tg.sent()[-1].reply_markup


async def test_open_search_screen_and_resume_draft(harness):
    h = harness
    await register(h)
    await say(h, t.BTN_SEARCH)
    assert t.SEARCH_NO_PARAMS in h.tg.texts()[-1]
    await tap(h, "set", "auction_type=rent")
    assert "Продаж / Оренда: Оренда" in h.tg.texts()[-1]
    # Leave and come back: draft is resumed.
    await say(h, t.BTN_HELP)
    await say(h, t.BTN_SEARCH)
    assert "Продаж / Оренда: Оренда" in h.tg.texts()[-1]
    async with h.sf() as s:
        assert (
            await s.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "SEARCH_STARTED")
            )
            == 2
        )


async def test_search_requires_at_least_one_param(harness):
    h = harness
    await register(h)
    await tap(h, "run")
    assert answers(h)[-1].text == t.SEARCH_NEED_PARAM
    assert h.tender.calls == []  # API not called


async def test_regions_multiselect(harness):
    h = harness
    await register(h)
    kyiv, lviv = REGIONS.index("Київ"), REGIONS.index("Львівська область")
    await tap(h, "reg")
    await tap(h, "regt", str(lviv))
    await tap(h, "regt", str(kyiv))
    await tap(h, "regt", str(lviv))  # toggle off
    await tap(h, "regt", str(lviv))  # and on again
    assert (await draft(h))["regions"] == ["Київ", "Львівська область"]
    labels = [text for text, _ in buttons(last_markup(h))]
    assert "✅ Київ" in labels and "✅ Львівська область" in labels


async def test_category_from_api(harness):
    h = harness
    await register(h)
    await tap(h, "cat", "0")
    labels = [text for text, _ in buttons(last_markup(h))]
    assert "Нерухомість" in labels
    await tap(h, "catset", category_key("Транспорт"))
    assert (await draft(h))["category"] == "Транспорт"
    await tap(h, "catset", "")
    assert "category" not in await draft(h)


async def test_text_inputs_and_menu_override(harness):
    h = harness
    await register(h)
    await tap(h, "txt", "city")
    await say(h, "Біла Церква")
    assert (await draft(h))["city"] == "Біла Церква"
    assert "Місто: Біла Церква" in h.tg.texts()[-1]

    # A menu button while waiting for keywords cancels input; it is never stored.
    await tap(h, "txt", "keywords")
    await say(h, t.BTN_HELP)
    assert "keywords" not in await draft(h)
    await say(h, "трактор")  # no longer in input state
    assert "keywords" not in await draft(h)
    assert h.tg.texts()[-1] == t.UNKNOWN_INPUT


async def test_price_range_with_validation(harness):
    h = harness
    await register(h)
    await tap(h, "price")
    await say(h, "abc")
    assert h.tg.texts()[-1] == t.BAD_NUMBER
    await say(h, "100 000")
    assert h.tg.texts()[-1] == t.PROMPT_MAX_PRICE
    await say(h, "50000")
    assert "Максимальне значення не може бути меншим" in h.tg.texts()[-1]
    await say(h, "250000")
    d = await draft(h)
    assert d["min_price"] == "100000" and d["max_price"] == "250000"


async def test_price_skip_both_bounds_means_not_set(harness):
    h = harness
    await register(h)
    await tap(h, "price")
    await tap(h, "skip")
    await tap(h, "skip")
    assert "min_price" not in await draft(h)
    assert h.tg.texts()[-1].startswith(t.RANGE_NOT_SET)


async def test_area_unit_then_bounds(harness):
    h = harness
    await register(h)
    await tap(h, "area")
    await tap(h, "unit", "ha.")
    await tap(h, "skip")
    await say(h, "2,5")
    d = await draft(h)
    assert d["area_unit"] == "ha." and d["max_area"] == "2.5" and "min_area" not in d


async def test_clear_requires_confirmation(harness):
    h = harness
    await register(h)
    await tap(h, "set", "auction_type=sale")
    await tap(h, "clear")
    assert h.tg.texts()[-1] == t.CONFIRM_CLEAR
    assert await draft(h)  # nothing cleared yet
    await tap(h, "clearok")
    assert await draft(h) == {}


async def run_search(h, **params) -> SearchSnapshot:
    for key, value in params.items():
        await tap(h, "set", f"{key}={value}")
    h.tg.clear()
    await tap(h, "run")
    async with h.sf() as s:
        return (await s.scalars(select(SearchSnapshot).order_by(SearchSnapshot.id.desc()))).first()


async def test_search_results_pagination_and_links(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")  # mock: 60 results -> 2 pages
    assert h.tender.calls[-1] == ("auctions", {"auction_type": "sale", "page": 1, "pagesize": 50})
    texts = h.tg.texts()
    assert "Знайдено аукціонів: <b>60</b>" in texts[0]
    assert len(texts) >= 2  # 50 items split across several Telegram messages
    assert all(len(x) <= 4096 for x in texts)
    assert sum(x.count(t.RESULT_LINK) for x in texts) == 50
    assert "https://tg-test.example.com/r/" in texts[0]  # tracked links
    # Keyboard only under the last message, with "Показати ще".
    sent = h.tg.sent()
    assert all(m.reply_markup is None for m in sent[:-1])
    more = RCb(a="more", s=snap.id, p=2).pack()
    assert (t.BTN_MORE, more) in buttons(sent[-1].reply_markup)

    h.tg.clear()
    await h.feed(callback_update(UID, more))
    assert sum(x.count(t.RESULT_LINK) for x in h.tg.texts()) == 10
    final = [m for m in h.tg.sent() if m.reply_markup][-1]
    assert t.BTN_MORE not in [text for text, _ in buttons(final.reply_markup)]  # last page
    assert any(isinstance(r, EditMessageReplyMarkup) for r in h.tg.requests)  # old "More" removed

    # Double tap on the old "Показати ще" sends nothing new.
    h.tg.clear()
    await h.feed(callback_update(UID, more))
    assert h.tg.texts() == []
    assert answers(h)[-1].text == t.PAGE_ALREADY_SHOWN


async def test_zero_results(harness):
    h = harness
    await register(h)
    await tap(h, "txt", "keywords")
    await say(h, "нічого такого")
    h.tg.clear()
    await tap(h, "run")
    assert h.tg.texts()[-1].endswith(t.ZERO_RESULTS)
    labels = [text for text, _ in buttons(last_markup(h))]
    assert labels == [t.BTN_ENABLE_MONITORING, t.BTN_CHANGE_PARAMS, t.BTN_NEW_SEARCH]


async def test_old_snapshot_buttons_use_old_filters(harness):
    h = harness
    await register(h)
    snap_a = await run_search(h, auction_type="sale")
    await tap(h, "clearok")
    await run_search(h, auction_type="rent")  # Search B
    await h.feed(callback_update(UID, RCb(a="more", s=snap_a.id, p=2).pack()))
    assert h.tender.calls[-1][1]["auction_type"] == "sale"  # uses snapshot A


async def test_change_params_from_old_result_asks_before_overwriting(harness):
    h = harness
    await register(h)
    snap_a = await run_search(h, auction_type="sale")
    await tap(h, "set", "auction_type=rent")  # unfinished new draft
    h.tg.clear()
    await h.feed(callback_update(UID, RCb(a="edit", s=snap_a.id).pack()))
    assert h.tg.texts() == [t.CONFIRM_REPLACE_DRAFT]
    assert (await draft(h))["auction_type"] == "rent"  # not overwritten yet
    await h.feed(callback_update(UID, RCb(a="editok", s=snap_a.id).pack()))
    assert (await draft(h))["auction_type"] == "sale"


async def test_new_search_without_unsaved_changes_clears_immediately(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    await h.feed(callback_update(UID, RCb(a="new", s=snap.id).pack()))
    assert await draft(h) == {}


async def test_api_error_keeps_draft_and_offers_retry(harness):
    h = harness
    await register(h)
    h.tender.fail_with = "unavailable"
    snap = await run_search(h, auction_type="sale")
    assert h.tg.texts()[-1] == t.API_ERROR
    assert (await draft(h))["auction_type"] == "sale"
    retry = RCb(a="retry", s=snap.id, p=1).pack()
    assert (t.BTN_RETRY, retry) in buttons(last_markup(h))
    h.tender.fail_with = None
    h.tg.clear()
    await h.feed(callback_update(UID, retry))
    assert "Знайдено аукціонів" in h.tg.texts()[0]


async def test_other_users_snapshot_is_rejected(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    other = 5005
    await h.feed(contact_update(other, contact_user_id=other))
    h.tg.clear()
    await h.feed(callback_update(other, RCb(a="more", s=snap.id, p=2).pack()))
    assert h.tg.texts() == []
    assert [r for r in h.tg.requests if isinstance(r, AnswerCallbackQuery)][
        -1
    ].text == t.STALE_ACTION


async def test_search_completed_event(harness):
    h = harness
    await register(h)
    await run_search(h, auction_type="sale")
    async with h.sf() as s:
        ev = await s.scalar(select(Event).where(Event.event_type == "SEARCH_COMPLETED"))
    assert ev.event_data["items_count"] == 60
