"""Iteration 8: editable Help texts, manager card retries, operational alerts."""

from __future__ import annotations

from aiogram.methods import SendMessage
from sqlalchemy import select

from app.api.main import THETENDER_WEBHOOK_PATH
from app.bot import keyboards as kb
from app.bot.handlers.consultation import CCb
from app.models import ROLE_ADMIN, ROLE_MANAGER, ConsultationNotification
from app.services import alerts
from app.services import consultations as consult_svc
from app.workers.delivery import DeliveryWorker
from tests.conftest import make_settings
from tests.fake_telegram import callback_update
from tests.test_admin_dashboard import ADMIN_TG, CLIENT_TG, OTHER_TG, dashboard, register


def sends(d, chat_id: int) -> list[SendMessage]:
    return [m for m in d.tg.requests if isinstance(m, SendMessage) and int(m.chat_id) == chat_id]


async def test_help_texts_are_editable_in_the_dashboard(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await register(d, CLIENT_TG)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        page = await d.client.get("/admin/settings", params={"tab": "texts"})
        assert "<h2>Тексти бота</h2>" in page.text and "стандартний текст" in page.text

        r = await d.client.post(
            "/admin/settings/texts/HELP_FAQ", data={"csrf": csrf, "value": "<b>Питання</b"}
        )
        assert "text_error=" in r.headers["location"]
        r = await d.client.post(
            "/admin/settings/texts/HELP_FAQ",
            data={"csrf": csrf, "value": "<b>Часті питання</b>\nЯк зареєструватися?"},
        )
        assert "msg=saved" in r.headers["location"]

        d.tg.clear()
        await d.dp.feed_update(d.bot, callback_update(CLIENT_TG, kb.HelpCb(section="faq").pack()))
        assert d.tg.texts()[-1] == "<b>Часті питання</b>\nЯк зареєструватися?"

        await d.client.post(
            "/admin/settings/texts/HELP_FAQ", data={"csrf": csrf, "action": "reset"}
        )
        d.tg.clear()
        await d.dp.feed_update(d.bot, callback_update(CLIENT_TG, kb.HelpCb(section="faq").pack()))
        assert "Часті запитання" in d.tg.texts()[-1]  # built-in default again
        assert (
            await d.client.post("/admin/settings/texts/NOPE", data={"csrf": csrf})
        ).status_code == 404


async def test_failed_manager_card_is_resent(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, OTHER_TG, ROLE_MANAGER)
        client = await register(d, CLIENT_TG)
        d.tg.fail_chats.add(OTHER_TG)  # the manager's chat is unavailable at first
        await d.dp.feed_update(d.bot, callback_update(CLIENT_TG, CCb(a="req").pack()))
        async with d.sf() as s:
            req = await consult_svc.get_open_request(s, client.id)
            note = await s.scalar(select(ConsultationNotification))
            assert req is not None and note.status == "FAILED"

        d.tg.fail_chats.clear()
        worker = DeliveryWorker(
            d.bot, d.sf, make_settings(), global_interval=0, per_chat_interval=0
        )
        d.tg.clear()
        assert await worker._retry_manager_notifications() == 1
        (card,) = sends(d, OTHER_TG)
        assert card.text.startswith("🆕 <b>Нова заявка!</b>")
        assert [b.text for row in card.reply_markup.inline_keyboard for b in row] == [
            "✅ Опрацювати"
        ]
        async with d.sf() as s:
            note = await s.scalar(select(ConsultationNotification))
            assert note.status == "SENT" and note.message_id
        assert await worker._retry_manager_notifications() == 0  # nothing left


async def test_alerts_reach_admins_once_per_hour(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        d.tg.clear()
        for _ in range(3):
            r = await d.client.post(
                THETENDER_WEBHOOK_PATH, params={"auth": "wrong"}, json={"event_type": "x"}
            )
            assert r.status_code == 403
        await alerts.drain()
        msgs = sends(d, ADMIN_TG)
        assert len(msgs) == 1 and "Технічне сповіщення бота" in msgs[0].text
        assert "неправильним ключем" in msgs[0].text


async def test_errors_are_visible_in_the_dashboard(session_factory):
    import logging

    from app.services import error_log

    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await d.login(ADMIN_TG)
        try:
            raise ValueError("boom with ?auth=SECRET123")
        except ValueError:
            logging.getLogger("app.test").exception(
                "Something failed for token 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            )
        await error_log.drain()
        page = await d.client.get("/admin/errors")
        assert "Something failed" in page.text and "ValueError" in page.text
        assert "SECRET123" not in page.text and "AAAAAAAAAAAAAAAAAAAA" not in page.text


async def test_help_section_can_be_a_link_or_a_document(session_factory):
    from aiogram.methods import SendDocument
    from aiogram.types import BufferedInputFile

    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await register(d, CLIENT_TG)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        url = "/admin/settings/texts/HELP_TARIFFS"

        # Link with a description; the URL is required.
        r = await d.client.post(url, data={"csrf": csrf, "mode": "link", "value": "Тарифи"})
        assert "text_error=" in r.headers["location"]
        r = await d.client.post(
            url,
            data={
                "csrf": csrf,
                "mode": "link",
                "value": "Актуальні тарифи — на сайті.",
                "button_text": "💳 Усі тарифи",
                "button_url": "https://thetender.com.ua/tariffs",
            },
        )
        assert "msg=saved" in r.headers["location"]
        d.tg.clear()

        def tap():  # a fresh update each time (duplicate update ids are dropped)
            return callback_update(CLIENT_TG, kb.HelpCb(section="tariffs").pack())

        await d.dp.feed_update(d.bot, tap())
        msg = d.tg.sent()[-1]
        assert msg.text == "Актуальні тарифи — на сайті."
        button = msg.reply_markup.inline_keyboard[0][0]
        assert button.text == "💳 Усі тарифи"
        assert button.url.startswith("https://thetender.com.ua/tariffs?utm_source=telegram")
        assert "utm_campaign=support&utm_content=tariffs" in button.url

        # Document: a file is required; bad types are refused.
        r = await d.client.post(url, data={"csrf": csrf, "mode": "doc", "value": ""})
        assert "text_error=" in r.headers["location"]
        r = await d.client.post(
            url,
            data={"csrf": csrf, "mode": "doc", "value": ""},
            files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
        )
        assert "text_error=" in r.headers["location"]
        r = await d.client.post(
            url,
            data={"csrf": csrf, "mode": "doc", "value": "Прайс у PDF"},
            files={"file": ("tariffs.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        assert "msg=saved" in r.headers["location"]
        page = await d.client.get("/admin/settings", params={"tab": "texts"})
        assert "tariffs.pdf" in page.text
        got = await d.client.get(f"{url}/file")
        assert got.content == b"%PDF-1.4 test"

        d.tg.clear()
        await d.dp.feed_update(d.bot, tap())
        (first,) = [m for m in d.tg.requests if isinstance(m, SendDocument)]
        assert isinstance(first.document, BufferedInputFile) and first.caption == "Прайс у PDF"
        # The second time the cached Telegram file_id is used, no re-upload.
        d.tg.clear()
        await d.dp.feed_update(d.bot, tap())
        (second,) = [m for m in d.tg.requests if isinstance(m, SendDocument)]
        assert isinstance(second.document, str) and second.document.startswith("FILE")

        # Re-saving the description keeps the file.
        r = await d.client.post(url, data={"csrf": csrf, "mode": "doc", "value": "Новий опис"})
        assert "msg=saved" in r.headers["location"]

        # Back to plain text.
        await d.client.post(url, data={"csrf": csrf, "mode": "text", "value": "Тарифи: від 0 грн"})
        d.tg.clear()
        await d.dp.feed_update(d.bot, tap())
        assert d.tg.texts()[-1] == "Тарифи: від 0 грн"
