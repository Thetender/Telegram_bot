from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import func, select

from app.bot import texts as t
from app.bot.handlers.consultation import CCb
from app.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ConsultationNotification,
    ConsultationRequest,
    Event,
    ManagerSettings,
    User,
)
from app.services import consultations as svc
from app.services import users as users_svc
from tests.fake_telegram import callback_update, contact_update, message_update

CLIENT, M1, M2, ADMIN = 7001, 7002, 7003, 7004


async def register(h, uid: int, role: str | None = None) -> None:
    await h.feed(contact_update(uid, contact_user_id=uid, phone=f"38067{uid:07d}"))
    if role:
        async with h.sf() as s:
            user = await s.scalar(select(User).where(User.telegram_id == uid))
            await users_svc.grant_role(s, user, role, actor_user_id=None)
            await s.commit()


async def setup_people(h) -> None:
    await register(h, CLIENT)
    await register(h, M1, ROLE_MANAGER)
    await register(h, M2, ROLE_MANAGER)
    await register(h, ADMIN, ROLE_ADMIN)
    h.tg.clear()


async def tap(h, uid: int, a: str, r: int = 0, v: str = "") -> None:
    await h.feed(callback_update(uid, CCb(a=a, r=r, v=v).pack()))


async def request_id(h) -> int:
    async with h.sf() as s:
        return await s.scalar(select(func.max(ConsultationRequest.id)))


async def get_req(h, rid: int) -> ConsultationRequest:
    async with h.sf() as s:
        return await s.get(ConsultationRequest, rid)


def alerts(h) -> list[str]:
    return [r.text for r in h.tg.requests if isinstance(r, AnswerCallbackQuery) and r.text]


def test_weekend_rule():
    assert svc.is_weekend(datetime(2026, 10, 10, 9, tzinfo=UTC), "Europe/Kyiv")  # Saturday
    assert not svc.is_weekend(datetime(2026, 10, 9, 9, tzinfo=UTC), "Europe/Kyiv")  # Friday
    # Sunday 22:30 UTC is already Monday 01:30 in Kyiv
    assert not svc.is_weekend(datetime(2026, 10, 11, 22, 30, tzinfo=UTC), "Europe/Kyiv")


async def test_menu_shows_registered_phone(harness):
    h = harness
    await setup_people(h)
    await h.feed(message_update(CLIENT, t.BTN_CONSULTATION))
    msg = h.tg.sent()[-1]
    assert "+38067" in msg.text
    assert msg.reply_markup.inline_keyboard[0][0].text == t.BTN_ORDER_CONSULTATION


async def test_request_notifies_managers_and_is_unique(harness, monkeypatch):
    h = harness
    await setup_people(h)
    monkeypatch.setattr(svc, "is_weekend", lambda now, tz: False)
    await tap(h, CLIENT, "req")
    texts = [m.text for m in h.tg.to(CLIENT)]
    assert t.CONSULT_WORKDAY.format(company_phone="+38 067 333 78 00") in texts
    for manager in (M1, M2):
        note = h.tg.to(manager)[-1]
        assert note.text.startswith(t.REQ_NEW_HEADER)
        assert note.reply_markup.inline_keyboard[0][0].text == t.BTN_CLAIM
    assert h.tg.to(ADMIN) == []  # admins only as fallback

    # Second request while the first is open: no duplicate.
    h.tg.clear()
    await tap(h, CLIENT, "req")
    assert h.tg.to(CLIENT)[-1].text.startswith("Ваша заявка на консультацію вже прийнята")
    async with h.sf() as s:
        assert await s.scalar(select(func.count()).select_from(ConsultationRequest)) == 1
        assert (
            await s.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.event_type == "CONSULTATION_REQUESTED")
            )
            == 1
        )


async def test_weekend_copy(harness, monkeypatch):
    h = harness
    await setup_people(h)
    monkeypatch.setattr(svc, "is_weekend", lambda now, tz: True)
    await tap(h, CLIENT, "req")
    assert t.CONSULT_WEEKEND.format(company_phone="+38 067 333 78 00") in [
        m.text for m in h.tg.to(CLIENT)
    ]


async def test_claim_updates_other_managers_and_result_completes(harness):
    h = harness
    await setup_people(h)
    await tap(h, CLIENT, "req")
    rid = await request_id(h)
    h.tg.clear()

    await tap(h, M1, "claim", rid)
    req = await get_req(h, rid)
    assert req.status == "IN_PROGRESS"
    edits = [r for r in h.tg.requests if isinstance(r, EditMessageText)]
    m1_edit = [e for e in edits if int(e.chat_id) == M1][-1]
    m2_edit = [e for e in edits if int(e.chat_id) == M2][-1]
    assert t.REQ_IN_PROGRESS_MINE in m1_edit.text
    assert "Взяв в роботу" in m2_edit.text and m2_edit.reply_markup is None

    # Second manager tries to claim later: told who took it.
    await tap(h, M2, "claim", rid)
    assert any("вже взяв в роботу" in a for a in alerts(h))
    assert (await get_req(h, rid)).claimed_by != (await get_req(h, rid)).user_id

    # M2 cannot set the result of M1's request.
    await tap(h, M2, "res", rid, "INTERESTED")
    assert (await get_req(h, rid)).status == "IN_PROGRESS"

    await tap(h, M1, "res", rid, "INTERESTED")
    req = await get_req(h, rid)
    assert req.status == "COMPLETED" and req.result == "INTERESTED"
    # Completed result is read-only for the manager.
    await tap(h, M1, "res", rid, "DECLINED")
    assert (await get_req(h, rid)).result == "INTERESTED"

    # Client may now request again.
    h.tg.clear()
    await tap(h, CLIENT, "req")
    async with h.sf() as s:
        assert await s.scalar(select(func.count()).select_from(ConsultationRequest)) == 2


async def test_concurrent_claim_has_exactly_one_winner(harness):
    h = harness
    await setup_people(h)
    await tap(h, CLIENT, "req")
    rid = await request_id(h)
    await asyncio.gather(tap(h, M1, "claim", rid), tap(h, M2, "claim", rid))
    async with h.sf() as s:
        taken = await s.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "CONSULTATION_TAKEN")
        )
        req = await s.get(ConsultationRequest, rid)
        winner = await s.get(User, req.claimed_by)
    assert taken == 1
    assert winner.telegram_id in (M1, M2)


async def test_manager_has_no_active_limit(harness):
    h = harness
    await setup_people(h)
    other_clients = [7101, 7102]
    for c in other_clients:
        await register(h, c)
    for c in [CLIENT, *other_clients]:
        await tap(h, c, "req")
    async with h.sf() as s:
        ids = (await s.scalars(select(ConsultationRequest.id))).all()
    for rid in ids:
        await tap(h, M1, "claim", rid)
    async with h.sf() as s:
        in_progress = await s.scalar(
            select(func.count())
            .select_from(ConsultationRequest)
            .where(ConsultationRequest.status == "IN_PROGRESS")
        )
    assert in_progress == 3


async def test_fallback_to_admins_when_no_manager_reachable(harness):
    h = harness
    await setup_people(h)
    h.tg.fail_chats = {M1}
    async with h.sf() as s:
        m2 = await s.scalar(select(User).where(User.telegram_id == M2))
        await s.merge(ManagerSettings(user_id=m2.id, consultation_notifications_enabled=False))
        await s.commit()
    await tap(h, CLIENT, "req")
    assert h.tg.to(M2) == []  # notifications disabled
    admin_msgs = h.tg.to(ADMIN)
    assert admin_msgs and admin_msgs[-1].text.startswith(t.ADMIN_FALLBACK_HEADER)
    async with h.sf() as s:
        failed = await s.scalar(
            select(func.count())
            .select_from(ConsultationNotification)
            .where(ConsultationNotification.status == "FAILED")
        )
    assert failed == 1


async def test_my_requests_lists_only_own(harness):
    h = harness
    await setup_people(h)
    await tap(h, CLIENT, "req")
    rid = await request_id(h)
    await tap(h, M1, "claim", rid)
    h.tg.clear()
    await h.feed(message_update(M1, t.BTN_MY_REQUESTS))
    labels = [b.text for row in h.tg.sent()[-1].reply_markup.inline_keyboard for b in row]
    assert any(label.startswith(f"№{rid}") for label in labels)
    h.tg.clear()
    await h.feed(message_update(M2, t.BTN_MY_REQUESTS))
    assert t.MY_REQUESTS_EMPTY_ACTIVE in h.tg.sent()[-1].text

    # Completed list after result.
    await tap(h, M1, "res", rid, "DECLINED")
    h.tg.clear()
    await tap(h, M1, "list", v="d")
    labels = [b.text for row in h.tg.sent()[-1].reply_markup.inline_keyboard for b in row]
    assert any(label.startswith(f"№{rid}") and "Відмова" in label for label in labels)


async def test_new_tab_lets_a_later_manager_take_an_unassigned_request(harness):
    """A request created when no manager existed is visible in «🆕 Нові»."""
    h = harness
    await register(h, CLIENT)
    await register(h, ADMIN, ROLE_ADMIN)
    await tap(h, CLIENT, "req")  # nobody but the admin (fallback) is notified
    rid = await request_id(h)
    await register(h, M1, ROLE_MANAGER)  # added later
    h.tg.clear()
    await tap(h, M1, "list", v="n")
    text = h.tg.sent()[-1].text
    labels = [b.text for row in h.tg.sent()[-1].reply_markup.inline_keyboard for b in row]
    assert t.BTN_NEW_REQUESTS in text
    assert any(label.startswith(f"№{rid}") for label in labels)

    await tap(h, M1, "view", rid)
    card = h.tg.sent()[-1]
    assert [b.text for row in card.reply_markup.inline_keyboard for b in row] == [t.BTN_CLAIM]
    await tap(h, M1, "claim", rid)
    assert (await get_req(h, rid)).status == "IN_PROGRESS"
    # The card opened from «Нові» was updated with the result buttons.
    edits = [r for r in h.tg.requests if isinstance(r, EditMessageText) and int(r.chat_id) == M1]
    assert edits and t.REQ_IN_PROGRESS_MINE in edits[-1].text

    h.tg.clear()
    await tap(h, M1, "list", v="n")
    assert t.MY_REQUESTS_EMPTY_NEW in h.tg.sent()[-1].text


async def test_non_manager_cannot_claim(harness):
    h = harness
    await setup_people(h)
    await tap(h, CLIENT, "req")
    rid = await request_id(h)
    await tap(h, CLIENT, "claim", rid)
    assert (await get_req(h, rid)).status == "NEW"


async def test_help_consultation_entry(harness):
    from app.bot import keyboards as kb

    h = harness
    await setup_people(h)
    await h.feed(callback_update(CLIENT, kb.HelpCb(section="consult").pack()))
    msgs = [m for m in h.tg.to(CLIENT) if isinstance(m, SendMessage)]
    assert msgs[-1].reply_markup.inline_keyboard[0][0].text == t.BTN_ORDER_CONSULTATION
