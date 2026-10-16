"""Help → «💬 Відгуки та пропозиції», admin notice, Dashboard thread and reply."""

from __future__ import annotations

from aiogram.methods import SendMessage
from sqlalchemy import select

from app.bot import keyboards as kb
from app.bot import texts as t
from app.bot.handlers.feedback import FbCb
from app.models import ROLE_ADMIN, Campaign, Feedback, FeedbackMessage
from app.services import campaigns as campaigns_svc
from tests.conftest import make_settings
from tests.fake_telegram import callback_update, message_update
from tests.test_admin_dashboard import ADMIN_TG, CLIENT_TG, dashboard, register


def sends(d, chat_id: int) -> list[SendMessage]:
    return [m for m in d.tg.requests if isinstance(m, SendMessage) and int(m.chat_id) == chat_id]


async def test_feedback_flow_with_admin_reply_and_follow_up(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        client = await register(d, CLIENT_TG)
        await d.dp.feed_update(d.bot, callback_update(CLIENT_TG, kb.HelpCb(section="fb").pack()))
        topics = [b.text for row in d.tg.sent()[-1].reply_markup.inline_keyboard for b in row]
        assert topics[:4] == [
            "🐞 Повідомити про помилку",
            "💡 Запропонувати покращення",
            "🔎 Проблема з пошуком",
            "✍️ Інше повідомлення",
        ]
        await d.dp.feed_update(
            d.bot, callback_update(CLIENT_TG, FbCb(a="topic", v="search").pack())
        )
        assert t.BTN_BACK_TO_TOPICS in str(d.tg.sent()[-1].reply_markup)
        d.tg.clear()
        await d.dp.feed_update(
            d.bot, message_update(CLIENT_TG, "Не знаходить https://thetender.com.ua/auction/X")
        )
        assert sends(d, CLIENT_TG)[-1].text == t.FEEDBACK_THANKS
        (notice,) = sends(d, ADMIN_TG)
        assert "Нове звернення №" in notice.text and "Проблема з пошуком" in notice.text
        async with d.sf() as s:
            fb = await s.scalar(select(Feedback))
            assert fb.user_id == client.id and fb.status == "NEW" and fb.topic == "search"

        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        page = await d.client.get("/admin/feedback")
        assert "Не знаходить" in page.text and "Робота з клієнтами" in page.text
        page = await d.client.get(f"/admin/feedback/{fb.id}")
        assert "Листування" in page.text
        d.tg.clear()
        r = await d.client.post(
            f"/admin/feedback/{fb.id}/reply",
            data={"csrf": csrf, "text": "Виправили <b>!", "resolve": "1"},
        )
        assert "msg=reply_sent" in r.headers["location"]
        (answer,) = sends(d, CLIENT_TG)
        assert "Відповідь команди THE TENDER" in answer.text and "&lt;b&gt;" in answer.text
        async with d.sf() as s:
            assert (await s.get(Feedback, fb.id)).status == "RESOLVED"

        # The person answers back: the thread reopens and admins are told.
        d.tg.clear()
        await d.dp.feed_update(
            d.bot, callback_update(CLIENT_TG, FbCb(a="reply", v=str(fb.id)).pack())
        )
        await d.dp.feed_update(d.bot, message_update(CLIENT_TG, "Дякую, працює"))
        assert "Нове повідомлення у зверненні" in sends(d, ADMIN_TG)[-1].text
        async with d.sf() as s:
            assert (await s.get(Feedback, fb.id)).status == "IN_PROGRESS"
            texts = [
                m.text
                for m in await s.scalars(select(FeedbackMessage).order_by(FeedbackMessage.id))
            ]
        assert texts == [
            "Не знаходить https://thetender.com.ua/auction/X",
            "Виправили <b>!",
            "Дякую, працює",
        ]


def test_campaign_can_invite_to_feedback():
    c = Campaign(id=5, title="x", message="Новини", feedback_invite=True)
    text, markup = campaigns_svc.render(c, 1, make_settings())
    assert text.endswith(t.FEEDBACK_INVITE)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == [t.BTN_LEAVE_FEEDBACK, "🔕 Відписатися від розсилок"]
