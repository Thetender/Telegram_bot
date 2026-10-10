"""Iteration 7: marketing campaigns, plus admin navigation improvements."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram.methods import AnswerCallbackQuery, SendMessage
from sqlalchemy import func, select

from app.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    Campaign,
    CampaignRecipient,
    Event,
    NotificationDelivery,
    User,
)
from app.services import campaigns as svc
from app.services import users as users_svc
from app.workers.delivery import DeliveryWorker
from tests.conftest import make_settings
from tests.fake_telegram import callback_update
from tests.test_admin_dashboard import ADMIN_TG, CLIENT_TG, OTHER_TG, dashboard, register

U4, U5 = 9004, 9005


def camp(**kw) -> Campaign:
    data = {"id": 1, "title": "x", "message": "Привіт", "audience_type": "ALL"}
    data.update(kw)
    return Campaign(**data)


def test_validation_of_telegram_formatting():
    assert svc.validate(camp(message="<b>Жирний</b> і <a href='https://x.ua'>лінк</a>")) == []
    assert any("не підтримується" in e for e in svc.validate(camp(message="<h1>x</h1>")))
    assert any("Не всі теги закрито" in e for e in svc.validate(camp(message="<b>x")))
    assert any("https://" in e for e in svc.validate(camp(message="<a href='javascript:x'>a</a>")))
    assert svc.validate(camp(message="", button_text="Так"))  # no text, button half-filled
    errors = svc.validate(camp(button_text="Так", button_url="http://x.ua"))
    assert any("https://" in e for e in errors)
    assert svc.validate(camp(audience_type="SELECTED", selected_user_ids=[]))
    assert any("задовгий" in e for e in svc.validate(camp(message="я" * 4000)))


def test_preview_is_sanitized():
    out = svc.preview_html(
        '<b onclick="alert(1)">x</b><script>bad()</script><a href="javascript:1">y</a>'
    )
    assert "onclick" not in out and "<script" not in out and "javascript" not in out
    assert out.startswith("<b>x</b>")


async def make_people(d) -> dict[str, User]:
    people = {
        "admin": await register(d, ADMIN_TG, ROLE_ADMIN),
        "a": await register(d, CLIENT_TG),
        "b": await register(d, OTHER_TG),
        "optout": await register(d, U4),
        "gone": await register(d, U5),
    }
    async with d.sf() as s:
        await users_svc.set_marketing_opt_out(s, await s.get(User, people["optout"].id), True)
        (await s.get(User, people["gone"].id)).bot_blocked_at = datetime.now(UTC)
        await s.commit()
    return people


async def test_full_campaign_flow(session_factory):
    async with dashboard(session_factory) as d:
        people = await make_people(d)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.post("/admin/campaigns/new", data={"csrf": csrf})
        cid = int(r.headers["location"].rsplit("/", 1)[1])
        form = {
            "csrf": csrf,
            "title": "Жовтневі аукціони",
            "message": "<b>Нові аукціони</b>\nДивіться на сайті.",
            "button_text": "Переглянути",
            "button_url": "https://thetender.com.ua/auctions",
            "audience_type": "ALL",
        }
        await d.client.post(f"/admin/campaigns/{cid}/save", data=form)
        page = await d.client.get(f"/admin/campaigns/{cid}")
        assert "Отримають повідомлення" in page.text
        async with d.sf() as s:
            aud = await svc.audience(s, await s.get(Campaign, cid))
        # admin + a + b receive; optout and gone are excluded.
        assert (aud.total, aud.opted_out, aud.unreachable, aud.final) == (5, 1, 1, 3)

        # Test message goes only to the admin.
        d.tg.clear()
        r = await d.client.post(f"/admin/campaigns/{cid}/test", data={"csrf": csrf})
        assert "msg=campaign_test_sent" in r.headers["location"]
        (test_msg,) = [m for m in d.tg.requests if isinstance(m, SendMessage)]
        assert int(test_msg.chat_id) == ADMIN_TG and "Тестове повідомлення" in test_msg.text

        # Confirmation is refused when the numbers on the page are stale.
        r = await d.client.post(
            f"/admin/campaigns/{cid}/confirm", data={"csrf": csrf, "expected": 99}
        )
        assert "msg=campaign_audience_changed" in r.headers["location"]
        r = await d.client.post(
            f"/admin/campaigns/{cid}/confirm", data={"csrf": csrf, "expected": 3}
        )
        assert "msg=campaign_queued" in r.headers["location"]
        # Locked after confirmation.
        r = await d.client.post(f"/admin/campaigns/{cid}/save", data=form)
        assert "msg=campaign_locked" in r.headers["location"]

        # A user who opts out after confirmation is skipped.
        async with d.sf() as s:
            await users_svc.set_marketing_opt_out(s, await s.get(User, people["b"].id), True)
            await s.commit()
        settings = make_settings(
            public_base_url="https://tg.example.com", telegram_webhook_secret="s3cret"
        )
        worker = DeliveryWorker(d.bot, d.sf, settings, global_interval=0, per_chat_interval=0)
        d.tg.clear()
        assert await worker.run_once() == 3
        sends = [m for m in d.tg.requests if isinstance(m, SendMessage)]
        assert sorted(int(m.chat_id) for m in sends) == [ADMIN_TG, CLIENT_TG]
        msg = [m for m in sends if int(m.chat_id) == CLIENT_TG][0]
        assert msg.text.startswith("<b>Нові аукціони</b>")
        buttons = [b for row in msg.reply_markup.inline_keyboard for b in row]
        assert buttons[0].text == "Переглянути"
        assert buttons[0].url.startswith("https://tg.example.com/r/")  # tracked click
        assert buttons[1].callback_data == f"mu:{cid}"

        async with d.sf() as s:
            c = await s.get(Campaign, cid)
            assert c.status == "DONE" and c.recipients_count == 3
            statuses = dict(
                (await s.execute(select(CampaignRecipient.user_id, CampaignRecipient.status))).all()
            )
            assert statuses[people["b"].id] == "SKIPPED"
            assert await s.scalar(select(Event).where(Event.event_type == "CAMPAIGN_SENT"))

        # A click through the tracked button is counted once per user.
        path = buttons[0].url.removeprefix("https://tg.example.com")
        browser = {"user-agent": "Mozilla/5.0 (iPhone)"}
        await d.client.get(path, headers=browser)
        await d.client.get(path, headers=browser)
        page = await d.client.get(f"/admin/campaigns/{cid}")
        assert "Переходи за кнопкою" in page.text
        async with d.sf() as s:
            stats = (await svc.progress(s, [cid]))[cid]
        assert (stats.sent, stats.skipped, stats.clicked) == (2, 1, 1)
        listing = await d.client.get("/admin/campaigns")
        assert "Жовтневі аукціони" in listing.text and "Надіслано" in listing.text


async def test_selected_audience_still_excludes_opted_out(session_factory):
    async with dashboard(session_factory) as d:
        people = await make_people(d)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.post("/admin/campaigns/new", data={"csrf": csrf})
        cid = int(r.headers["location"].rsplit("/", 1)[1])
        page = await d.client.get(f"/admin/campaigns/{cid}", params={"q": "Іван"})
        assert page.status_code == 200
        await d.client.post(
            f"/admin/campaigns/{cid}/select",
            data={"csrf": csrf, "add": [people["a"].id, people["optout"].id]},
        )
        async with d.sf() as s:
            c = await s.get(Campaign, cid)
            assert c.audience_type == "SELECTED"
            aud = await svc.audience(s, c)
        assert aud.recipient_ids == [people["a"].id] and aud.opted_out == 1
        await d.client.post(
            f"/admin/campaigns/{cid}/select", data={"csrf": csrf, "remove": people["a"].id}
        )
        async with d.sf() as s:
            assert (await s.get(Campaign, cid)).selected_user_ids == [people["optout"].id]
        r = await d.client.post(f"/admin/campaigns/{cid}/delete", data={"csrf": csrf})
        assert "msg=campaign_deleted" in r.headers["location"]


async def test_notifications_have_priority_over_campaigns(session_factory):
    async with dashboard(session_factory) as d:
        people = await make_people(d)
        async with d.sf() as s:
            c = Campaign(title="t", message="Привіт", audience_type="ALL")
            s.add(c)
            await s.flush()
            await svc.confirm(s, c, await s.get(User, people["admin"].id))
            s.add(
                NotificationDelivery(
                    canonical_auction_id="LSE-1",
                    telegram_user_id=CLIENT_TG,
                    monitoring_name="m",
                    auction={"name": "Аукціон", "url": "https://thetender.com.ua/a/1"},
                )
            )
            await s.commit()
        worker = DeliveryWorker(
            d.bot, d.sf, make_settings(), global_interval=0, per_chat_interval=0
        )
        d.tg.clear()
        await worker.run_once()
        sends = [m for m in d.tg.requests if isinstance(m, SendMessage)]
        assert len(sends) == 1 and "Новий аукціон" in sends[0].text
        await worker.run_once()
        async with d.sf() as s:
            left = await s.scalar(
                select(func.count())
                .select_from(CampaignRecipient)
                .where(CampaignRecipient.status == "PENDING")
            )
        assert left == 0


async def test_unsubscribe_button(session_factory):
    async with dashboard(session_factory) as d:
        people = await make_people(d)
        d.tg.clear()
        await d.dp.feed_update(d.bot, callback_update(CLIENT_TG, "mu:5"))
        async with d.sf() as s:
            assert (await s.get(User, people["a"].id)).marketing_opt_out_at is not None
        alerts = [r for r in d.tg.requests if isinstance(r, AnswerCallbackQuery)]
        assert alerts and alerts[0].show_alert and "відписалися" in alerts[0].text


async def test_users_list_role_buttons_return_to_the_list(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        client = await register(d, CLIENT_TG)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        page = await d.client.get("/admin/users")
        assert "Зробити менеджером" in page.text and "Зробити адміном" in page.text
        r = await d.client.post(
            "/admin/settings/managers/add",
            data={"csrf": csrf, "user_id": client.id, "next": "/admin/users?page=1"},
        )
        assert r.headers["location"].startswith("/admin/users?page=1&msg=manager_added")
        async with d.sf() as s:
            assert ROLE_MANAGER in await users_svc.get_roles(s, client.id)
        r = await d.client.post(
            "/admin/settings/admins/invite",
            data={"csrf": csrf, "user_id": client.id, "next": "/admin/users?page=1"},
        )
        assert r.headers["location"].startswith("/admin/users?page=1&msg=invite")
        page = await d.client.get("/admin/users")
        assert "запрошено в адміни" in page.text and "Зняти менеджера" in page.text
        # Foreign return addresses are ignored.
        r = await d.client.post(
            f"/admin/users/{client.id}/manager",
            data={"csrf": csrf, "action": "revoke", "next": "https://evil.example/"},
        )
        assert r.headers["location"].startswith(f"/admin/users/{client.id}")


async def test_settings_tabs(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await d.login(ADMIN_TG)
        page = await d.client.get("/admin/settings")
        assert "<h2>Адміністратори</h2>" in page.text and "<h2>Менеджери</h2>" not in page.text
        for tab, title in [
            ("managers", "Менеджери"),
            ("legal", "Юридичні документи"),
            ("privacy", "Запити на видалення персональних даних"),
        ]:
            page = await d.client.get("/admin/settings", params={"tab": tab})
            assert f"<h2>{title}</h2>" in page.text
