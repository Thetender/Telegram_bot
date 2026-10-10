"""Iteration 6: legal re-acceptance gate and remote-first personal-data deletion."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram.methods import AnswerCallbackQuery, SendMessage
from sqlalchemy import select

from app.bot import keyboards as kb
from app.bot import texts as t
from app.bot.handlers.search import SCb
from app.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ConsultationRequest,
    Monitoring,
    PrivacyRequest,
    SearchDraft,
    User,
)
from app.services import consultations as consult_svc
from app.services import legal
from app.services import monitorings as mon_svc
from app.services import privacy as privacy_svc
from tests.fake_telegram import callback_update, contact_update, message_update
from tests.test_admin_dashboard import ADMIN_TG, CLIENT_TG, dashboard, register

UID = 6006


async def publish(sf, doc_type: str, version: str, reaccept: bool) -> None:
    async with sf() as s:
        await legal.publish_version(
            s,
            doc_type,
            version,
            public_url=f"https://thetender.com.ua/{doc_type.lower()}-{version}",
            stored_reference=f"{doc_type}-{version}",
            effective_at=datetime.now(UTC),
            require_reacceptance=reaccept,
        )
        await s.commit()


def texts_to(h, uid: int) -> list[str]:
    return [m.text for m in h.tg.to(uid)]


async def test_reacceptance_gate_blocks_interactive_functions_until_accepted(harness):
    h = harness
    await publish(h.sf, "TERMS", "1.0", reaccept=False)
    await h.feed(contact_update(UID, contact_user_id=UID))
    await publish(h.sf, "TERMS", "2.0", reaccept=True)
    await publish(h.sf, "PRIVACY", "1.1", reaccept=False)  # does not require confirmation
    h.tg.clear()

    await h.feed(message_update(UID, t.BTN_SEARCH))
    (gate,) = h.tg.sent()
    assert gate.text.startswith("📄 <b>Ми оновили Умови використання</b>")
    urls = [b.url for row in gate.reply_markup.inline_keyboard for b in row if b.url]
    assert urls == ["https://thetender.com.ua/terms-2.0"]

    # Old callbacks are gated too (and the callback is answered).
    h.tg.clear()
    await h.feed(callback_update(UID, SCb(a="open").pack()))
    assert [m.text for m in h.tg.sent()][0].startswith("📄 <b>Ми оновили")
    assert any(isinstance(r, AnswerCallbackQuery) for r in h.tg.requests)

    # Allowed while gated: marketing preference and the deletion request screen.
    h.tg.clear()
    await h.feed(callback_update(UID, kb.HelpCb(section="settings").pack()))
    assert h.tg.texts()[-1].startswith(t.MARKETING_TITLE)
    await h.feed(callback_update(UID, kb.MarketingCb(enable=False).pack()))
    async with h.sf() as s:
        user = await s.scalar(select(User).where(User.telegram_id == UID))
        assert user.marketing_opt_out_at is not None
    await h.feed(callback_update(UID, kb.HelpCb(section="delete").pack()))
    assert h.tg.texts()[-1] == t.DELETE_DATA_EXPLANATION

    # Accept → main menu, then everything works again.
    h.tg.clear()
    await h.feed(callback_update(UID, kb.LegalCb(action="accept").pack()))
    assert h.tg.texts() == [t.LEGAL_ACCEPTED_TEXT, t.MAIN_MENU]
    h.tg.clear()
    await h.feed(message_update(UID, t.BTN_SEARCH))
    assert t.SEARCH_TITLE in h.tg.texts()[-1]
    async with h.sf() as s:
        assert await legal.pending_reacceptance(s, user.id) == []


async def test_new_users_accept_current_versions_at_registration(harness):
    h = harness
    await publish(h.sf, "TERMS", "2.0", reaccept=True)
    await publish(h.sf, "PRIVACY", "2.0", reaccept=True)
    await h.feed(contact_update(UID, contact_user_id=UID))
    h.tg.clear()
    await h.feed(message_update(UID, t.BTN_SEARCH))
    assert t.SEARCH_TITLE in h.tg.texts()[-1]  # no gate


async def test_gate_names_both_documents(harness):
    h = harness
    await h.feed(contact_update(UID, contact_user_id=UID))
    await publish(h.sf, "TERMS", "2.0", reaccept=True)
    await publish(h.sf, "PRIVACY", "2.0", reaccept=True)
    h.tg.clear()
    await h.feed(message_update(UID, "/start"))
    assert "Умови використання та Політику конфіденційності" in h.tg.texts()[-1]


async def test_deletion_request_notifies_admins(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await register(d, CLIENT_TG)
        d.tg.clear()
        await d.dp.feed_update(
            d.bot, callback_update(CLIENT_TG, kb.PrivacyCb(action="send").pack())
        )
        notices = [
            m for m in d.tg.requests if isinstance(m, SendMessage) and int(m.chat_id) == ADMIN_TG
        ]
        assert len(notices) == 1 and "Новий запит на видалення" in notices[0].text
        assert "https://tg.example.com/admin/settings#privacy" in notices[0].text


async def test_remote_first_deletion_with_retry(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        client = await register(d, CLIENT_TG)
        tender = d.dp["tender"]
        async with d.sf() as s:
            user = await s.get(User, client.id)
            await mon_svc.create(s, tender, user, "Склади", {"auction_type": "sale"})
            await consult_svc.create_request(s, user)
            s.add(SearchDraft(user_id=user.id, params={"city": "Київ"}))
            req, _ = await privacy_svc.create_deletion_request(s, user)
            await s.commit()
            rid = req.id
        await d.login(ADMIN_TG)
        csrf = await d.csrf()

        # The Tender is down: nothing local is touched, the request stays in progress.
        tender.fail_monitoring["list"] = "unavailable"
        r = await d.client.post(f"/admin/privacy/{rid}/process", data={"csrf": csrf})
        assert "msg=privacy_failed" in r.headers["location"]
        async with d.sf() as s:
            req = await s.get(PrivacyRequest, rid)
            assert req.status == "IN_PROGRESS" and req.attempts == 1 and req.last_error
            assert (await s.get(User, client.id)).phone is not None
        page = await d.client.get("/admin/settings")
        assert "Повторити" in page.text

        # Retry succeeds: monitorings deleted remotely first, then local anonymization.
        del tender.fail_monitoring["list"]
        d.tg.clear()
        r = await d.client.post(f"/admin/privacy/{rid}/process", data={"csrf": csrf})
        assert "msg=privacy_done" in r.headers["location"]
        assert await tender.list_monitorings(str(CLIENT_TG)) == []
        async with d.sf() as s:
            req = await s.get(PrivacyRequest, rid)
            assert req.status == "COMPLETED" and req.attempts == 2 and req.last_error is None
            u = await s.get(User, client.id)
            assert (u.name, u.phone, u.username) == (None, None, None) and u.anonymized_at
            c = await s.scalar(
                select(ConsultationRequest).where(ConsultationRequest.user_id == u.id)
            )
            assert (c.contact_name, c.contact_phone) == (None, None)
            assert await s.get(SearchDraft, u.id) is None
            assert await s.scalar(select(Monitoring).where(Monitoring.deleted_at.is_(None))) is None
        (bye,) = [m for m in d.tg.requests if isinstance(m, SendMessage)]
        assert int(bye.chat_id) == CLIENT_TG and bye.text.startswith("Ваші персональні дані")

        # The person is treated as a new visitor afterwards.
        d.tg.clear()
        await d.dp.feed_update(d.bot, message_update(CLIENT_TG, t.BTN_SEARCH))
        assert t.REGISTRATION_REQUIRED in [
            m.text for m in d.tg.requests if isinstance(m, SendMessage)
        ]


async def test_staff_must_lose_role_before_deletion(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        manager = await register(d, CLIENT_TG, ROLE_MANAGER)
        async with d.sf() as s:
            req, _ = await privacy_svc.create_deletion_request(s, await s.get(User, manager.id))
            await s.commit()
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.post(f"/admin/privacy/{req.id}/process", data={"csrf": csrf})
        assert "msg=privacy_staff" in r.headers["location"]
        async with d.sf() as s:
            assert (await s.get(PrivacyRequest, req.id)).status == "NEW"
