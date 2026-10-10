"""Monitorings in the bot: create from a search, list, detail, results,
edit with a separate draft and explicit save, rename, delete (FR §7)."""

from __future__ import annotations

from sqlalchemy import select

from app.bot import texts as t
from app.bot.callbacks import MCb
from app.bot.handlers.results import RCb
from app.bot.handlers.search import SCb
from app.models import Event, Monitoring, MonitoringEditDraft, SearchSnapshot, User
from app.services import monitorings as mon_svc
from app.services.search_params import REGIONS, from_api, to_monitoring_body
from tests.fake_telegram import callback_update, contact_update
from tests.test_search_flow import UID, buttons, draft, last_markup, register, say, tap


async def mtap(h, a: str, i: int = 0, p: int = 0) -> None:
    await h.feed(callback_update(UID, MCb(a=a, i=i, p=p).pack()))


async def etap(h, a: str, v: str = "") -> None:
    """Search screens in monitoring-edit mode."""
    await h.feed(callback_update(UID, SCb(a=a, v=v, d="e").pack()))


async def run_search(h, **params) -> SearchSnapshot:
    for key, value in params.items():
        await tap(h, "set", f"{key}={value}")
    await tap(h, "run")
    async with h.sf() as s:
        return (await s.scalars(select(SearchSnapshot).order_by(SearchSnapshot.id.desc()))).first()


async def local(h) -> list[Monitoring]:
    async with h.sf() as s:
        return list(
            await s.scalars(
                select(Monitoring).where(Monitoring.deleted_at.is_(None)).order_by(Monitoring.id)
            )
        )


def test_monitoring_body_is_full_replacement_and_readback_parses():
    body = to_monitoring_body({"auction_type": "sale", "regions": ["Київ", "Львівська область"]})
    assert body["region"] == ["Київ", "Львівська область"]
    assert body["city"] is None and body["min_price"] is None  # cleared fields sent as null
    assert set(body) == {
        "auction_type", "start_price_type", "category", "region", "city", "keywords",
        "customer_name", "min_price", "max_price", "area_unit", "min_area", "max_area",
    }  # fmt: skip
    back = from_api(
        {
            "name": "x",
            "auction_type": "sale",
            "region": "Київ,Львівська область",
            "min_price": "100000.00",
            "area_unit": "ha.",
            "max_area": 2.5,
            "city": None,
        }
    )
    assert back == {
        "auction_type": "sale",
        "regions": ["Київ", "Львівська область"],
        "min_price": "100000",
        "area_unit": "ha.",
        "max_area": "2.5",
    }


async def test_create_from_search_with_typed_name(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    h.tg.clear()
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    assert h.tg.texts()[-1] == t.MON_ASK_NAME
    suggestion = mon_svc.suggested_name(snap.params)
    assert t.BTN_USE_NAME.format(name=suggestion) in [b for b, _ in buttons(last_markup(h))]

    # A menu button is never taken as the name.
    await say(h, t.BTN_HELP)
    assert await local(h) == []
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "x" * 101)
    assert h.tg.texts()[-1] == t.MON_BAD_NAME
    await say(h, "  Мій   склад ")
    rows = await local(h)
    assert [(r.name, r.params) for r in rows] == [("Мій склад", {"auction_type": "sale"})]
    assert h.tg.texts()[-2].startswith("Моніторинг «<b>Мій склад</b>» створено ✅")
    assert h.tg.texts()[-1] == t.MAIN_MENU  # straight back to the main menu
    create = [c for c in h.tender.calls if c[0] == "monitoring_create"][-1][1]
    assert create["user_id"] == str(UID) and create["name"] == "Мій склад"
    async with h.sf() as s:
        assert await s.scalar(select(Event).where(Event.event_type == "MONITOR_CREATED"))
    # The search that became a monitoring is cleared for the next one.
    assert await draft(h) == {}


async def test_create_with_suggested_name_and_results_of_monitoring(harness):
    h = harness
    await register(h)
    await tap(h, "reg")
    await tap(h, "regt", str(REGIONS.index("Київ")))
    await tap(h, "open")
    snap = await run_search(h, auction_type="rent")
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await mtap(h, "usename", snap.id)
    (row,) = await local(h)
    assert row.name == "Оренда · Київ"
    assert row.params == {"auction_type": "rent", "regions": ["Київ"]}

    # «Показати результати» runs the saved filters and offers no «Увімкнути моніторинг».
    h.tg.clear()
    await mtap(h, "res", row.id)
    assert h.tender.calls[-1][0] == "auctions"
    assert h.tender.calls[-1][1]["region"] == "Київ"
    labels = [text for text, _ in buttons(last_markup(h))]
    assert t.BTN_ENABLE_MONITORING not in labels


async def test_api_failure_never_reports_success(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    h.tender.fail_monitoring["create"] = "unavailable"
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "Склади")
    assert h.tg.texts()[-1] == t.MON_SAVE_RETRY
    assert await local(h) == []
    # The name prompt is still active: sending it again works once the API is back.
    del h.tender.fail_monitoring["create"]
    await say(h, "Склади")
    assert [r.name for r in await local(h)] == ["Склади"]


async def test_ambiguous_create_is_reconciled_not_duplicated(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    h.tender.fail_monitoring["create_ambiguous"] = "1"
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "Склади")
    creates = [c for c in h.tender.calls if c[0] == "monitoring_create"]
    assert len(creates) == 1  # no blind second create
    assert [r.name for r in await local(h)] == ["Склади"]  # adopted from the list
    assert "створено ✅" in h.tg.texts()[-2]


async def test_list_detail_edit_save_rename_delete(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "Продаж")
    await tap(h, "set", "auction_type=rent")  # unfinished search draft
    (row,) = await local(h)

    h.tg.clear()
    await say(h, t.BTN_MONITORINGS)
    assert h.tg.texts()[-1].startswith(f"{t.MON_TITLE} (1)")
    assert ("🔔 Продаж", MCb(a="show", i=row.id).pack()) in buttons(last_markup(h))

    await mtap(h, "show", row.id)
    assert "• Продаж / Оренда: Продаж" in h.tg.texts()[-1]

    # Edit: own draft, search draft untouched.
    await mtap(h, "edit", row.id)
    assert h.tg.texts()[-1].startswith(t.MON_EDIT_TITLE)
    await etap(h, "grid")
    await etap(h, "txt", "city")
    await say(h, "Біла Церква")
    assert "• Місто: Біла Церква" in h.tg.texts()[-1]
    await etap(h, "set", "auction_type=rent")
    assert await draft(h) == {"auction_type": "rent"}  # search draft unchanged
    async with h.sf() as s:
        edit = await s.get(MonitoringEditDraft, row.user_id)
        assert edit.params == {"auction_type": "rent", "city": "Біла Церква"}
        assert (await s.get(Monitoring, row.id)).params == {"auction_type": "sale"}  # not saved yet

    await mtap(h, "rename")
    await say(h, "Оренда БЦ")
    assert "Назва: <b>Оренда БЦ</b>" in h.tg.texts()[-1]

    await mtap(h, "save")
    update = [c for c in h.tender.calls if c[0] == "monitoring_update"][-1][1]
    assert update["name"] == "Оренда БЦ" and update["city"] == "Біла Церква"
    assert update["keywords"] is None  # full replacement
    async with h.sf() as s:
        saved = await s.get(Monitoring, row.id)
        assert saved.name == "Оренда БЦ"
        assert saved.params == {"auction_type": "rent", "city": "Біла Церква"}
        assert await s.get(MonitoringEditDraft, row.user_id) is None
    assert h.tg.texts()[-1].startswith(t.MON_SAVED)

    # Delete with confirmation.
    await mtap(h, "del", row.id)
    assert "Видалити моніторинг" in h.tg.texts()[-1]
    await mtap(h, "delok", row.id)
    assert await local(h) == []
    assert h.tg.texts()[-1].startswith("Моніторинг «<b>Оренда БЦ</b>» видалено.")
    assert t.MON_EMPTY in h.tg.texts()[-1]


async def test_edit_save_requires_a_filter_and_cancel_discards(harness):
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "Продаж")
    (row,) = await local(h)
    await mtap(h, "edit", row.id)
    await etap(h, "fclr", "auction_type")
    assert t.MON_EDIT_NO_PARAMS in h.tg.texts()[-1]
    calls = len(h.tender.calls)
    await mtap(h, "save")
    assert len(h.tender.calls) == calls  # nothing sent to the API
    await mtap(h, "cancel")
    async with h.sf() as s:
        assert (await s.get(Monitoring, row.id)).params == {"auction_type": "sale"}
        user = await s.scalar(select(User).where(User.telegram_id == UID))
        assert await s.get(MonitoringEditDraft, user.id) is None


async def test_list_is_canonical_from_the_tender(harness):
    """A monitoring removed on the backend disappears from the local index."""
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "Продаж")
    (row,) = await local(h)
    await h.tender.delete_monitoring(row.external_id)
    await say(h, t.BTN_MONITORINGS)
    assert t.MON_EMPTY in h.tg.texts()[-1]
    assert await local(h) == []
    # An old button of the removed monitoring is handled gracefully.
    await mtap(h, "show", row.id)
    assert t.MON_NOT_FOUND in h.tg.texts()[-1]


async def test_list_api_error_offers_retry(harness):
    h = harness
    await register(h)
    h.tender.fail_monitoring["list"] = "unavailable"
    await say(h, t.BTN_MONITORINGS)
    assert h.tg.texts()[-1] == t.MON_API_ERROR
    assert (t.BTN_RETRY, MCb(a="list").pack()) in buttons(last_markup(h))


async def test_monitoring_message_is_shown_for_another_users_id(harness):
    """Callbacks with someone else's monitoring id are treated as missing."""
    h = harness
    await register(h)
    snap = await run_search(h, auction_type="sale")
    await h.feed(callback_update(UID, RCb(a="mon", s=snap.id).pack()))
    await say(h, "Продаж")
    (row,) = await local(h)
    other = 5005
    await h.feed(contact_update(other, contact_user_id=other, phone="380501112233"))
    h.tg.clear()
    await h.feed(callback_update(other, MCb(a="delok", i=row.id).pack()))
    assert [r.id for r in await local(h)] == [row.id]  # untouched
