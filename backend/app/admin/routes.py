"""Admin Dashboard pages (server-rendered, FR §11, UI/UX §10)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.admin import auth
from app.config import Settings
from app.models import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ConsultationRequest,
    LegalDocumentVersion,
    User,
)
from app.services import admin as svc
from app.services import consultations as consult_svc
from app.services import events, legal
from app.services import users as users_svc

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

STATUS_LABELS = {
    "NEW": "Нова",
    "IN_PROGRESS": "В роботі",
    "COMPLETED": "Завершена",
    "CLOSED_BY_ADMIN": "Закрито адміністратором",
}
RESULT_LABELS = {"INTERESTED": "👍 Зацікавлений", "DECLINED": "👎 Відмова"}
EVENT_LABELS = {
    events.USER_REGISTERED: "Реєстрація",
    events.BOT_STARTED: "Відкрив бота",
    events.SEARCH_STARTED: "Відкрив пошук",
    events.SEARCH_COMPLETED: "Пошук",
    events.AUCTION_OPENED: "Відкрив аукціон",
    events.MONITOR_CREATED: "Створив моніторинг",
    events.MONITOR_UPDATED: "Змінив моніторинг",
    events.MONITOR_DELETED: "Видалив моніторинг",
    events.AUCTION_NOTIFICATION_SENT: "Сповіщення про аукціон",
    events.CONSULTATION_REQUESTED: "Замовив консультацію",
    events.CONSULTATION_TAKEN: "Консультацію взято в роботу",
    events.CONSULTATION_COMPLETED: "Консультацію завершено",
    events.CONSULTATION_CLOSED_BY_ADMIN: "Консультацію закрито адміністратором",
    events.CONSULTATION_RESULT_CORRECTED: "Результат консультації виправлено",
    events.MARKETING_PREFERENCE_CHANGED: "Маркетингові повідомлення",
    events.PRIVACY_REQUEST_CREATED: "Запит на видалення даних",
    events.ROLE_GRANTED: "Надано роль",
    events.ROLE_REVOKED: "Знято роль",
    events.ADMIN_INVITED: "Запрошено в адміністратори",
    events.ADMIN_INVITATION_ACCEPTED: "Прийняв запрошення адміністратора",
    events.ADMIN_INVITATION_DECLINED: "Відхилив запрошення адміністратора",
    events.ADMIN_LOGIN: "Вхід в адмінку",
    events.LEGAL_VERSION_PUBLISHED: "Опубліковано юр. документ",
    events.LEGAL_URL_CHANGED: "Змінено посилання на юр. документ",
    events.SETTINGS_CHANGED: "Змінено налаштування",
}
FLASH = {
    "invited": ("ok", "Запрошення надіслано в бот. Роль стане активною після підтвердження."),
    "invite_pending": ("info", "Цьому користувачу вже надіслано запрошення."),
    "already_admin": ("info", "Користувач уже є адміністратором."),
    "invite_failed": (
        "warn",
        "Запрошення збережено, але бот не зміг його доставити (користувач заблокував бота?).",
    ),
    "last_admin": ("warn", "Не можна прибрати останнього адміністратора."),
    "admin_removed": ("ok", "Адміністратора прибрано."),
    "manager_added": ("ok", "Роль менеджера надано."),
    "manager_removed": ("ok", "Роль менеджера знято."),
    "saved": ("ok", "Збережено ✅"),
    "closed": ("ok", "Заявку закрито адміністратором."),
    "not_closed": ("warn", "Заявку вже завершено або закрито."),
    "corrected": ("ok", "Результат виправлено (зміну записано в журнал)."),
    "published": ("ok", "Нову версію опубліковано."),
    "version_exists": ("warn", "Така версія вже існує. Версії незмінні — вкажіть новий номер."),
    "bad_url": ("warn", "Посилання має починатися з https://"),
    "file_too_big": ("warn", "Файл завеликий (максимум 10 МБ)."),
    "cancelled": ("ok", "Запрошення скасовано."),
}
MAX_LEGAL_FILE = 10 * 1024 * 1024


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DB = Annotated[AsyncSession, Depends(db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


class LoginRequired(Exception):
    pass


async def require_admin(request: Request, session: DB) -> auth.AdminContext:
    ctx = await auth.load_admin(request, session)
    if ctx is None:
        raise LoginRequired()
    return ctx


Admin = Annotated[auth.AdminContext, Depends(require_admin)]


def redirect(path: str, msg: str | None = None, **params: object) -> RedirectResponse:
    query = {k: v for k, v in params.items() if v not in (None, "")}
    if msg:
        query["msg"] = msg
    url = path + ("?" + urlencode(query) if query else "")
    return RedirectResponse(url, status_code=303)


def render(request: Request, name: str, ctx: auth.AdminContext | None, section: str, **data):
    settings: Settings = request.app.state.settings
    tz = ZoneInfo(settings.timezone)

    def fmt_dt(value: datetime | None, with_time: bool = True) -> str:
        if value is None:
            return "—"
        local = value.astimezone(tz)
        return local.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")

    flash = FLASH.get(request.query_params.get("msg", ""))
    return templates.TemplateResponse(
        request,
        name,
        {
            "admin": ctx.user if ctx else None,
            "csrf": ctx.csrf_token if ctx else "",
            "section": section,
            "flash": flash,
            "fmt_dt": fmt_dt,
            "status_labels": STATUS_LABELS,
            "result_labels": RESULT_LABELS,
            "event_labels": EVENT_LABELS,
            "environment": settings.environment,
            **data,
        },
    )


def create_router() -> APIRouter:
    router = APIRouter(prefix="/admin")

    # ---------------- auth ----------------

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, session: DB, error: str = "") -> Response:
        if await auth.load_admin(request, session):
            return redirect("/admin")
        settings: Settings = request.app.state.settings
        bot_username = await _bot_username(request)
        auth_url = f"{settings.public_base_url or ''}/admin/auth/telegram"
        return render(
            request,
            "login.html",
            None,
            "login",
            bot_username=bot_username,
            auth_url=auth_url,
            error=error,
        )

    @router.get("/auth/telegram")
    async def telegram_callback(request: Request, session: DB, settings: SettingsDep) -> Response:
        data = dict(request.query_params)
        try:
            tg_id = auth.verify_telegram_login(data, settings.telegram_bot_token.get_secret_value())
        except ValueError:
            return redirect("/admin/login", error="signature")
        user = await users_svc.get_by_telegram_id(session, tg_id)
        if user is None or user.anonymized_at is not None:
            return redirect("/admin/login", error="not_registered")
        if not await auth.is_admin(session, user.id):
            return redirect("/admin/login", error="not_admin")
        token = await auth.create_session(session, user)
        await events.log_event(session, events.ADMIN_LOGIN, user_id=user.id, actor_user_id=user.id)
        response = redirect("/admin")
        response.set_cookie(
            auth.COOKIE_NAME,
            token,
            max_age=int(auth.SESSION_TTL.total_seconds()),
            httponly=True,
            secure=(settings.public_base_url or "").startswith("https://"),
            samesite="lax",
            path="/admin",
        )
        return response

    @router.post("/logout")
    async def logout(request: Request, session: DB, ctx: Admin, csrf: str = Form("")) -> Response:
        auth.check_csrf(ctx, csrf)
        token = request.cookies.get(auth.COOKIE_NAME)
        if token:
            await auth.revoke_session(session, token)
        response = redirect("/admin/login")
        response.delete_cookie(auth.COOKIE_NAME, path="/admin")
        return response

    # ---------------- overview ----------------

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def overview(
        request: Request, session: DB, ctx: Admin, settings: SettingsDep, period: int = 7
    ) -> Response:
        period = period if period in (7, 30, 90) else 7
        data = await svc.overview(session, period, settings.timezone)
        peak = max([max(r, s) for _, r, s in data.series] + [1])
        return render(request, "overview.html", ctx, "overview", o=data, peak=peak)

    # ---------------- users ----------------

    @router.get("/users", response_class=HTMLResponse)
    async def users(
        request: Request,
        session: DB,
        ctx: Admin,
        q: str = "",
        reg_from: str = "",
        reg_to: str = "",
        active: str = "",
        page: int = 1,
    ) -> Response:
        rows, total = await svc.list_users(
            session,
            q=q,
            registered_from=_parse_date(reg_from),
            registered_to=_parse_date(reg_to),
            active_days=int(active) if active.isdigit() else None,
            page=page,
        )
        pages = max(1, -(-total // svc.PAGE_SIZE))
        base_query = urlencode(
            {
                k: v
                for k, v in {
                    "q": q,
                    "reg_from": reg_from,
                    "reg_to": reg_to,
                    "active": active,
                }.items()
                if v
            }
        )
        return render(
            request,
            "users.html",
            ctx,
            "users",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            q=q,
            reg_from=reg_from,
            reg_to=reg_to,
            active=active,
            base_query=base_query,
        )

    @router.get("/users/{user_id}", response_class=HTMLResponse)
    async def user_detail(request: Request, session: DB, ctx: Admin, user_id: int) -> Response:
        profile = await svc.user_profile(session, user_id)
        if profile is None:
            raise HTTPException(404)
        return render(request, "user.html", ctx, "users", p=profile)

    @router.post("/users/{user_id}/manager")
    async def user_manager_role(
        session: DB, ctx: Admin, user_id: int, csrf: str = Form(""), action: str = Form("")
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(404)
        if action == "grant":
            await users_svc.grant_role(session, user, ROLE_MANAGER, actor_user_id=ctx.user.id)
            return redirect(f"/admin/users/{user_id}", "manager_added")
        await users_svc.revoke_role(session, user, ROLE_MANAGER, actor_user_id=ctx.user.id)
        return redirect(f"/admin/users/{user_id}", "manager_removed")

    # ---------------- consultations ----------------

    @router.get("/consultations", response_class=HTMLResponse)
    async def consultations(
        request: Request, session: DB, ctx: Admin, tab: str = "all", page: int = 1
    ) -> Response:
        tab = tab if tab in svc.CONSULTATION_TABS else "all"
        rows, total, counts = await svc.list_consultations(session, tab, page)
        pages = max(1, -(-total // svc.PAGE_SIZE))
        return render(
            request,
            "consultations.html",
            ctx,
            "consultations",
            rows=rows,
            tab=tab,
            counts=counts,
            page=page,
            pages=pages,
        )

    @router.get("/consultations/{request_id}", response_class=HTMLResponse)
    async def consultation_detail(
        request: Request, session: DB, ctx: Admin, request_id: int
    ) -> Response:
        req = await session.get(ConsultationRequest, request_id)
        if req is None:
            raise HTTPException(404)
        client = await session.get(User, req.user_id)
        manager = await session.get(User, req.claimed_by) if req.claimed_by else None
        closer = await session.get(User, req.closed_by) if req.closed_by else None
        history = await _consultation_history(session, request_id)
        return render(
            request,
            "consultation.html",
            ctx,
            "consultations",
            req=req,
            client=client,
            manager=manager,
            closer=closer,
            history=history,
        )

    @router.post("/consultations/{request_id}/close")
    async def consultation_close(
        request: Request,
        session: DB,
        ctx: Admin,
        settings: SettingsDep,
        request_id: int,
        csrf: str = Form(""),
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        if not await svc.close_consultation(session, request_id, ctx.user):
            return redirect(f"/admin/consultations/{request_id}", "not_closed")
        await session.commit()
        await _update_manager_messages(request, session, settings, request_id)
        return redirect(f"/admin/consultations/{request_id}", "closed")

    @router.post("/consultations/{request_id}/result")
    async def consultation_correct(
        session: DB, ctx: Admin, request_id: int, csrf: str = Form(""), result: str = Form("")
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        if result not in RESULT_LABELS:
            raise HTTPException(400)
        changed = await svc.correct_result(session, request_id, ctx.user, result)
        return redirect(f"/admin/consultations/{request_id}", "corrected" if changed else None)

    # ---------------- campaigns (Iteration 7) ----------------

    @router.get("/campaigns", response_class=HTMLResponse)
    async def campaigns(request: Request, ctx: Admin) -> Response:
        return render(request, "campaigns.html", ctx, "campaigns")

    # ---------------- settings ----------------

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request, session: DB, ctx: Admin, find_admin: str = "", find_manager: str = ""
    ) -> Response:
        admins = await svc.staff(session, ROLE_ADMIN)
        managers = await svc.staff(session, ROLE_MANAGER)
        admin_found = await svc.find_registered(session, find_admin) if find_admin else None
        manager_found = await svc.find_registered(session, find_manager) if find_manager else None
        return render(
            request,
            "settings.html",
            ctx,
            "settings",
            admins=admins,
            managers=managers,
            invitations=await svc.pending_invitations(session),
            legal_versions=await svc.legal_versions(session),
            privacy=await svc.privacy_requests(session),
            find_admin=find_admin,
            admin_found=admin_found,
            find_manager=find_manager,
            manager_found=manager_found,
            admin_ids={r.user.id for r in admins},
            manager_ids={r.user.id for r in managers},
            today=datetime.now(UTC).date().isoformat(),
        )

    @router.post("/settings/admins/invite")
    async def invite_admin(
        request: Request,
        session: DB,
        ctx: Admin,
        settings: SettingsDep,
        csrf: str = Form(""),
        user_id: int = Form(...),
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        invited = await session.get(User, user_id)
        if invited is None or invited.anonymized_at is not None:
            raise HTTPException(404)
        invitation, status = await svc.create_invitation(session, invited, ctx.user)
        if status != "created" or invitation is None:
            msg = "already_admin" if status == "already_admin" else "invite_pending"
            return redirect("/admin/settings", msg)
        await session.commit()
        delivered = await _send_invitation(request, invited, ctx.user, invitation.id)
        return redirect("/admin/settings", "invited" if delivered else "invite_failed")

    @router.post("/settings/invitations/{invitation_id}/cancel")
    async def cancel_invite(
        session: DB, ctx: Admin, invitation_id: int, csrf: str = Form("")
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        await svc.cancel_invitation(session, invitation_id)
        return redirect("/admin/settings", "cancelled")

    @router.post("/settings/admins/{user_id}/remove")
    async def remove_admin(session: DB, ctx: Admin, user_id: int, csrf: str = Form("")) -> Response:
        auth.check_csrf(ctx, csrf)
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(404)
        try:
            await users_svc.revoke_role(session, user, ROLE_ADMIN, actor_user_id=ctx.user.id)
        except users_svc.LastAdminError:
            return redirect("/admin/settings", "last_admin")
        return redirect("/admin/settings", "admin_removed")

    @router.post("/settings/{role}/{user_id}/notifications")
    async def toggle_notifications(
        session: DB,
        ctx: Admin,
        role: str,
        user_id: int,
        csrf: str = Form(""),
        enabled: str = Form(""),
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        role_name = {"admins": ROLE_ADMIN, "managers": ROLE_MANAGER}.get(role)
        if role_name is None:
            raise HTTPException(404)
        await svc.set_notifications(session, user_id, role_name, enabled == "1", ctx.user)
        return redirect("/admin/settings", "saved")

    @router.post("/settings/managers/add")
    async def add_manager(
        session: DB, ctx: Admin, csrf: str = Form(""), user_id: int = Form(...)
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(404)
        await users_svc.grant_role(session, user, ROLE_MANAGER, actor_user_id=ctx.user.id)
        return redirect("/admin/settings", "manager_added")

    @router.post("/settings/managers/{user_id}/remove")
    async def remove_manager(
        session: DB, ctx: Admin, user_id: int, csrf: str = Form("")
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(404)
        await users_svc.revoke_role(session, user, ROLE_MANAGER, actor_user_id=ctx.user.id)
        return redirect("/admin/settings", "manager_removed")

    @router.post("/settings/legal/publish")
    async def legal_publish(
        session: DB,
        ctx: Admin,
        csrf: str = Form(""),
        doc_type: str = Form(...),
        version: str = Form(...),
        public_url: str = Form(...),
        effective_date: str = Form(""),
        reaccept: str = Form(""),
        file: UploadFile | None = File(None),  # noqa: B008
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        if doc_type not in (legal.TERMS, legal.PRIVACY) or not version.strip():
            raise HTTPException(400)
        if not public_url.startswith("https://"):
            return redirect("/admin/settings", "bad_url")
        content = None
        file_name = None
        if file is not None and file.filename:
            content = await file.read(MAX_LEGAL_FILE + 1)
            if len(content) > MAX_LEGAL_FILE:
                return redirect("/admin/settings", "file_too_big")
            file_name = file.filename[:255]
        effective = _parse_date(effective_date) or datetime.now(UTC).date()
        try:
            await legal.publish_version(
                session,
                doc_type=doc_type,
                version=version.strip()[:32],
                public_url=public_url.strip(),
                stored_reference=(
                    f"sha256:{svc.file_digest(content)}" if content else public_url.strip()
                ),
                effective_at=datetime(effective.year, effective.month, effective.day, tzinfo=UTC),
                require_reacceptance=reaccept == "1",
                file_name=file_name,
                file_content=content,
                created_by=ctx.user.id,
            )
        except ValueError:
            await session.rollback()
            return redirect("/admin/settings", "version_exists")
        return redirect("/admin/settings", "published")

    @router.post("/settings/legal/{version_id}/url")
    async def legal_url(
        session: DB, ctx: Admin, version_id: int, csrf: str = Form(""), public_url: str = Form(...)
    ) -> Response:
        auth.check_csrf(ctx, csrf)
        if not public_url.startswith("https://"):
            return redirect("/admin/settings", "bad_url")
        await svc.change_public_url(session, version_id, public_url.strip(), ctx.user)
        return redirect("/admin/settings", "saved")

    @router.get("/settings/legal/{version_id}/file")
    async def legal_file(session: DB, ctx: Admin, version_id: int) -> Response:
        version = await session.scalar(
            select(LegalDocumentVersion)
            .options(undefer(LegalDocumentVersion.file_content))
            .where(LegalDocumentVersion.id == version_id)
        )
        if version is None or not version.file_content:
            raise HTTPException(404)
        name = quote(version.file_name or f"document-{version_id}")
        return Response(
            version.file_content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
        )

    return router


# ---------------- helpers ----------------


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


async def _bot_username(request: Request) -> str | None:
    cached = getattr(request.app.state, "bot_username", None)
    if cached:
        return cached
    try:
        me = await request.app.state.bot.get_me()
        request.app.state.bot_username = getattr(me, "username", None)
    except Exception:  # noqa: BLE001
        log.warning("Could not get bot username for the login widget")
        return None
    return request.app.state.bot_username


async def _consultation_history(session: AsyncSession, request_id: int):
    from app.models import Event

    rows = await session.scalars(
        select(Event)
        .where(Event.event_data["request_id"].as_integer() == request_id)
        .order_by(Event.created_at)
    )
    events_list = list(rows)
    actor_ids = {e.actor_user_id for e in events_list if e.actor_user_id}
    actors = {}
    if actor_ids:
        actors = {
            u.id: u for u in await session.scalars(select(User).where(User.id.in_(actor_ids)))
        }
    return [(e, actors.get(e.actor_user_id)) for e in events_list]


async def _send_invitation(
    request: Request, invited: User, inviter: User, invitation_id: int
) -> bool:
    from app.bot.handlers.admin_invite import InvCb, invitation_text

    bot: Bot = request.app.state.bot
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Прийняти", callback_data=InvCb(a="y", i=invitation_id).pack()
                ),
                InlineKeyboardButton(
                    text="❌ Відхилити", callback_data=InvCb(a="n", i=invitation_id).pack()
                ),
            ]
        ]
    )
    try:
        await bot.send_message(invited.telegram_id, invitation_text(inviter), reply_markup=markup)
        return True
    except TelegramAPIError as exc:
        log.warning("Admin invitation %s not delivered: %s", invitation_id, exc)
        return False


async def _update_manager_messages(
    request: Request, session: AsyncSession, settings: Settings, request_id: int
) -> None:
    from app.bot import texts as t
    from app.bot.handlers.consultation import request_card

    bot: Bot = request.app.state.bot
    req = await session.get(ConsultationRequest, request_id)
    if req is None:
        return
    text = f"{request_card(req, settings.timezone)}\n\n{t.REQ_CLOSED_BY_ADMIN}"
    for note in await consult_svc.notifications_for(session, request_id):
        if note.message_id is None:
            continue
        try:
            await bot.edit_message_text(
                text=text, chat_id=note.chat_id, message_id=note.message_id, reply_markup=None
            )
        except TelegramAPIError:
            pass
