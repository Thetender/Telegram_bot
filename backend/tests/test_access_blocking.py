"""Closing bot access for a user (e.g. a competitor) from the Admin Dashboard."""

from __future__ import annotations

from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import select

from app.bot import texts as t
from app.models import ROLE_ADMIN, ROLE_MANAGER, BlockedPhone, ConsultationRequest, Event, User
from app.services import consultations as consult_svc
from tests.fake_telegram import contact_update, message_update
from tests.test_admin_dashboard import ADMIN_TG, CLIENT_TG, OTHER_TG, dashboard, register

SECOND_ACCOUNT_TG = 9010


def sent_to(d, chat_id: int) -> list[SendMessage]:
    return [m for m in d.tg.requests if isinstance(m, SendMessage) and int(m.chat_id) == chat_id]


async def test_block_closes_requests_notifies_once_and_covers_phone(session_factory):
    async with dashboard(session_factory) as d:
        admin = await register(d, ADMIN_TG, ROLE_ADMIN)
        manager = await register(d, OTHER_TG, ROLE_MANAGER)
        client = await register(d, CLIENT_TG)
        async with d.sf() as s:
            req, _ = await consult_svc.create_request(s, client)
            await consult_svc.record_notification(s, req.id, manager, "MANAGER", 777)
            await s.commit()
            rid = req.id

        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        page = await d.client.get(f"/admin/users/{client.id}")
        assert "Заблокувати доступ" in page.text
        d.tg.clear()
        r = await d.client.post(
            f"/admin/users/{client.id}/access",
            data={"csrf": csrf, "action": "block", "reason": "конкурент"},
        )
        assert "msg=blocked" in r.headers["location"]

        async with d.sf() as s:
            u = await s.get(User, client.id)
            assert u.access_blocked_at and u.access_blocked_by == admin.id
            assert u.access_block_reason == "конкурент"
            assert await s.get(BlockedPhone, client.phone)
            req = await s.get(ConsultationRequest, rid)
            assert req.status == "CLOSED_BY_ADMIN"
            assert await s.scalar(select(Event).where(Event.event_type == "USER_ACCESS_BLOCKED"))
        # The manager's card was updated.
        edits = [m for m in d.tg.requests if isinstance(m, EditMessageText)]
        assert edits and t.REQ_CLOSED_BY_ADMIN in edits[-1].text

        # The client gets one notice, then silence.
        d.tg.clear()
        await d.dp.feed_update(d.bot, message_update(CLIENT_TG, text=t.BTN_SEARCH))
        await d.dp.feed_update(d.bot, message_update(CLIENT_TG, text="/start"))
        msgs = sent_to(d, CLIENT_TG)
        assert len(msgs) == 1 and msgs[0].text.startswith("⛔️ Доступ до бота обмежено")

        # A new Telegram account with the same phone is blocked at registration.
        d.tg.clear()
        await d.dp.feed_update(
            d.bot,
            contact_update(SECOND_ACCOUNT_TG, SECOND_ACCOUNT_TG, phone=client.phone.lstrip("+")),
        )
        msgs = sent_to(d, SECOND_ACCOUNT_TG)
        assert [m.text.split("\n")[0] for m in msgs] == ["⛔️ Доступ до бота обмежено."]
        await d.dp.feed_update(d.bot, message_update(SECOND_ACCOUNT_TG, text=t.BTN_SEARCH))
        assert len(sent_to(d, SECOND_ACCOUNT_TG)) == 1

        # Users list: badge and filter.
        r = await d.client.get("/admin/users?blocked=1")
        assert "Заблоковано" in r.text and str(CLIENT_TG) in r.text and str(ADMIN_TG) not in r.text

        # Unblock restores access for both accounts and the phone.
        r = await d.client.post(
            f"/admin/users/{client.id}/access", data={"csrf": csrf, "action": "unblock"}
        )
        assert "msg=unblocked" in r.headers["location"]
        async with d.sf() as s:
            assert await s.get(BlockedPhone, client.phone) is None
            second = await s.scalar(select(User).where(User.telegram_id == SECOND_ACCOUNT_TG))
            assert second.access_blocked_at is None
        d.tg.clear()
        await d.dp.feed_update(d.bot, message_update(CLIENT_TG, text=t.BTN_SEARCH))
        assert sent_to(d, CLIENT_TG) and t.SEARCH_TITLE in sent_to(d, CLIENT_TG)[-1].text


async def test_cannot_block_staff_or_self(session_factory):
    async with dashboard(session_factory) as d:
        admin = await register(d, ADMIN_TG, ROLE_ADMIN)
        manager = await register(d, OTHER_TG, ROLE_MANAGER)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.post(
            f"/admin/users/{manager.id}/access", data={"csrf": csrf, "action": "block"}
        )
        assert "msg=block_staff" in r.headers["location"]
        r = await d.client.post(
            f"/admin/users/{admin.id}/access", data={"csrf": csrf, "action": "block"}
        )
        assert "msg=block_self" in r.headers["location"]
        async with d.sf() as s:
            assert (await s.get(User, manager.id)).access_blocked_at is None
            assert (await s.get(User, admin.id)).access_blocked_at is None
        # The page offers no block button for staff.
        page = await d.client.get(f"/admin/users/{manager.id}")
        assert "Заблокувати доступ" not in page.text


async def test_block_requires_csrf(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        client = await register(d, CLIENT_TG)
        await d.login(ADMIN_TG)
        r = await d.client.post(f"/admin/users/{client.id}/access", data={"action": "block"})
        assert r.status_code in (400, 403)
        async with d.sf() as s:
            assert (await s.get(User, client.id)).access_blocked_at is None
