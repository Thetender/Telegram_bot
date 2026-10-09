from __future__ import annotations

import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import func, select

from app.api.main import create_app
from app.bot.handlers.admin_invite import InvCb
from app.integrations.thetender import MockTenderClient
from app.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ConsultationRequest,
    Event,
    LegalDocumentVersion,
    User,
    UserRole,
)
from app.services import consultations as consult_svc
from app.services import users as users_svc
from tests.conftest import make_settings
from tests.fake_telegram import FakeSession, callback_update, contact_update, make_bot

BOT_TOKEN = "42:TEST_TOKEN_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ADMIN_TG, OTHER_TG, CLIENT_TG = 9001, 9002, 9003


def widget_data(tg_id: int, auth_date: int | None = None) -> dict[str, str]:
    data = {
        "id": str(tg_id),
        "first_name": "Адмін",
        "auth_date": str(auth_date or int(time.time())),
    }
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return data


@dataclass
class Dash:
    client: httpx.AsyncClient
    tg: FakeSession
    sf: object
    dp: object
    bot: object

    async def login(self, tg_id: int) -> httpx.Response:
        return await self.client.get("/admin/auth/telegram", params=widget_data(tg_id))

    async def csrf(self) -> str:
        r = await self.client.get("/admin/settings")
        marker = 'name="csrf" value="'
        start = r.text.index(marker) + len(marker)
        return r.text[start : r.text.index('"', start)]


@asynccontextmanager
async def dashboard(session_factory):
    bot, tg = make_bot()
    settings = make_settings(
        public_base_url="https://tg.example.com", telegram_webhook_secret="s3cret"
    )
    app = create_app(settings, bot=bot, tender=MockTenderClient())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://tg.example.com") as c:
            yield Dash(client=c, tg=tg, sf=session_factory, dp=app.state.dp, bot=bot)


async def register(d: Dash, tg_id: int, role: str | None = None) -> User:
    await d.dp.feed_update(
        d.bot, contact_update(tg_id, contact_user_id=tg_id, phone=f"38050{tg_id:07d}")
    )
    async with d.sf() as s:
        user = await s.scalar(select(User).where(User.telegram_id == tg_id))
        if role:
            await users_svc.grant_role(s, user, role, actor_user_id=None)
        await s.commit()
        return user


async def test_requires_login_and_shows_widget(session_factory):
    async with dashboard(session_factory) as d:
        r = await d.client.get("/admin")
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        r = await d.client.get("/admin/login")
        assert 'data-telegram-login="TgtestTT_bot"' in r.text
        assert 'data-auth-url="https://tg.example.com/admin/auth/telegram"' in r.text


async def test_login_rejects_bad_signature_expired_and_non_admin(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, OTHER_TG)
        bad = widget_data(OTHER_TG)
        bad["hash"] = "0" * 64
        r = await d.client.get("/admin/auth/telegram", params=bad)
        assert "error=signature" in r.headers["location"]
        old = widget_data(OTHER_TG, auth_date=int(time.time()) - 3600)
        r = await d.client.get("/admin/auth/telegram", params=old)
        assert "error=signature" in r.headers["location"]
        r = await d.login(OTHER_TG)
        assert "error=not_admin" in r.headers["location"]
        r = await d.login(12345)
        assert "error=not_registered" in r.headers["location"]
        assert (await d.client.get("/admin")).status_code == 303


async def test_admin_pages_and_cookie_flags(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await register(d, CLIENT_TG)
        r = await d.login(ADMIN_TG)
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        for path in [
            "/admin",
            "/admin?period=30",
            "/admin/users",
            "/admin/consultations",
            "/admin/settings",
            "/admin/campaigns",
        ]:
            resp = await d.client.get(path)
            assert resp.status_code == 200, path
            assert resp.headers["x-frame-options"] == "DENY"
        r = await d.client.get("/admin/users", params={"q": f"{CLIENT_TG:07d}"[-7:]})
        assert "+38050" in r.text
        async with d.sf() as s:
            client = await s.scalar(select(User).where(User.telegram_id == CLIENT_TG))
        r = await d.client.get(f"/admin/users/{client.id}")
        assert "Реєстрація" in r.text


async def test_csrf_required(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        client = await register(d, CLIENT_TG)
        await d.login(ADMIN_TG)
        r = await d.client.post(f"/admin/users/{client.id}/manager", data={"action": "grant"})
        assert r.status_code == 403
        async with d.sf() as s:
            assert (
                await s.scalar(
                    select(func.count()).select_from(UserRole).where(UserRole.role == ROLE_MANAGER)
                )
                == 0
            )


async def test_manager_role_and_last_admin_protection(session_factory):
    async with dashboard(session_factory) as d:
        admin = await register(d, ADMIN_TG, ROLE_ADMIN)
        client = await register(d, CLIENT_TG)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        await d.client.post(
            f"/admin/users/{client.id}/manager", data={"csrf": csrf, "action": "grant"}
        )
        async with d.sf() as s:
            assert ROLE_MANAGER in await users_svc.get_roles(s, client.id)
        await d.client.post(
            f"/admin/users/{client.id}/manager", data={"csrf": csrf, "action": "revoke"}
        )
        async with d.sf() as s:
            assert ROLE_MANAGER not in await users_svc.get_roles(s, client.id)
            audit = (await s.scalars(select(Event).where(Event.event_type == "ROLE_REVOKED"))).all()
            assert audit[0].actor_user_id == admin.id

        r = await d.client.post(f"/admin/settings/admins/{admin.id}/remove", data={"csrf": csrf})
        assert "msg=last_admin" in r.headers["location"]
        async with d.sf() as s:
            assert await users_svc.count_admins(s) == 1


async def test_invitation_flow_and_removed_admin_loses_access(session_factory):
    async with dashboard(session_factory) as d:
        admin = await register(d, ADMIN_TG, ROLE_ADMIN)
        other = await register(d, OTHER_TG)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.get("/admin/settings", params={"find_admin": f"+38050{OTHER_TG:07d}"})
        assert "Надіслати запрошення" in r.text
        d.tg.clear()
        r = await d.client.post(
            "/admin/settings/admins/invite", data={"csrf": csrf, "user_id": other.id}
        )
        assert "msg=invited" in r.headers["location"]
        invite_msg = [
            m for m in d.tg.sent() if isinstance(m, SendMessage) and int(m.chat_id) == OTHER_TG
        ][-1]
        accept = invite_msg.reply_markup.inline_keyboard[0][0].callback_data
        async with d.sf() as s:
            assert ROLE_ADMIN not in await users_svc.get_roles(s, other.id)  # not before accepting
        await d.dp.feed_update(d.bot, callback_update(OTHER_TG, accept))
        async with d.sf() as s:
            assert ROLE_ADMIN in await users_svc.get_roles(s, other.id)
        # Stale second tap
        await d.dp.feed_update(d.bot, callback_update(OTHER_TG, InvCb(a="y", i=999).pack()))

        # Now the first admin can be removed by the second; their session stops working.
        async with dashboard(session_factory) as d2:
            await d2.login(OTHER_TG)
            csrf2 = await d2.csrf()
            r = await d2.client.post(
                f"/admin/settings/admins/{admin.id}/remove", data={"csrf": csrf2}
            )
            assert "msg=admin_removed" in r.headers["location"]
        assert (await d.client.get("/admin")).status_code == 303


async def test_close_and_correct_consultation(session_factory):
    async with dashboard(session_factory) as d:
        admin = await register(d, ADMIN_TG, ROLE_ADMIN)
        manager = await register(d, OTHER_TG, ROLE_MANAGER)
        client = await register(d, CLIENT_TG)
        async with d.sf() as s:
            req, _ = await consult_svc.create_request(s, client)
            await consult_svc.record_notification(s, req.id, manager, "MANAGER", 555)
            await consult_svc.claim(s, req.id, manager)
            await s.commit()
            rid = req.id
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.get(f"/admin/consultations/{rid}")
        assert "Закрити адміністратором" in r.text
        d.tg.clear()
        r = await d.client.post(f"/admin/consultations/{rid}/close", data={"csrf": csrf})
        assert "msg=closed" in r.headers["location"]
        async with d.sf() as s:
            req = await s.get(ConsultationRequest, rid)
            assert (
                req.status == "CLOSED_BY_ADMIN" and req.result is None and req.closed_by == admin.id
            )
            # Client can request again.
            again, created = await consult_svc.create_request(s, client)
            assert created
            await consult_svc.claim(s, again.id, manager)
            await consult_svc.complete(s, again.id, manager, "DECLINED")
            await s.commit()
            rid2 = again.id
        edits = [m for m in d.tg.requests if isinstance(m, EditMessageText)]
        assert edits and "Закрито адміністратором" in edits[-1].text

        await d.client.post(
            f"/admin/consultations/{rid2}/result", data={"csrf": csrf, "result": "INTERESTED"}
        )
        async with d.sf() as s:
            assert (await s.get(ConsultationRequest, rid2)).result == "INTERESTED"
            ev = await s.scalar(
                select(Event).where(Event.event_type == "CONSULTATION_RESULT_CORRECTED")
            )
            assert ev.event_data == {
                "request_id": rid2,
                "previous": "DECLINED",
                "new": "INTERESTED",
            }
            assert ev.actor_user_id == admin.id


async def test_legal_documents_publish_url_and_file(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        form = {
            "csrf": csrf,
            "doc_type": "TERMS",
            "version": "1.0",
            "public_url": "https://thetender.com.ua/terms",
            "effective_date": "2026-10-09",
        }
        r = await d.client.post(
            "/admin/settings/legal/publish",
            data=form,
            files={"file": ("terms.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        assert "msg=published" in r.headers["location"]
        r = await d.client.post("/admin/settings/legal/publish", data=form)
        assert "msg=version_exists" in r.headers["location"]
        r = await d.client.post(
            "/admin/settings/legal/publish",
            data={**form, "version": "1.1", "public_url": "http://x"},
        )
        assert "msg=bad_url" in r.headers["location"]

        async with d.sf() as s:
            v = await s.scalar(select(LegalDocumentVersion))
        r = await d.client.get(f"/admin/settings/legal/{v.id}/file")
        assert r.content == b"%PDF-1.4 test"
        await d.client.post(
            f"/admin/settings/legal/{v.id}/url",
            data={"csrf": csrf, "public_url": "https://thetender.com.ua/new-terms"},
        )
        async with d.sf() as s:
            versions = (await s.scalars(select(LegalDocumentVersion))).all()
            assert (
                len(versions) == 1
                and versions[0].public_url == "https://thetender.com.ua/new-terms"
            )

        # The bot registration screen now links to the document.
        d.tg.clear()
        from tests.fake_telegram import message_update

        await d.dp.feed_update(d.bot, message_update(77777, "/start"))
        links = d.tg.sent()[0].reply_markup.inline_keyboard
        assert links[0][0].url == "https://thetender.com.ua/new-terms"


async def test_logout(session_factory):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await d.login(ADMIN_TG)
        csrf = await d.csrf()
        r = await d.client.post("/admin/logout", data={"csrf": csrf})
        assert r.headers["location"] == "/admin/login"
        assert (await d.client.get("/admin")).status_code == 303


@pytest.mark.parametrize("period", ["7", "30", "90", "13"])
async def test_overview_periods(session_factory, period):
    async with dashboard(session_factory) as d:
        await register(d, ADMIN_TG, ROLE_ADMIN)
        await d.login(ADMIN_TG)
        r = await d.client.get("/admin", params={"period": period})
        assert r.status_code == 200 and "Активність по днях" in r.text
