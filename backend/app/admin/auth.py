"""Dashboard authentication: Telegram Login Widget + server-side sessions.

- Identity is verified with the Telegram Login Widget signature
  (HMAC-SHA256, key = SHA256(bot token)); only users with the ADMIN role get in.
- The browser receives an httpOnly, Secure, SameSite=Lax cookie with a random
  token; only its SHA-256 hash is stored (admin_sessions).
- Every state-changing form carries a per-session CSRF token.
- Authorization (ADMIN role) is re-checked on every request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ROLE_ADMIN, AdminSession, User, UserRole

COOKIE_NAME = "tt_admin"
SESSION_TTL = timedelta(hours=12)
AUTH_MAX_AGE_SECONDS = 600  # a login link from Telegram is valid for 10 minutes


def verify_telegram_login(data: dict[str, str], bot_token: str, now: float | None = None) -> int:
    """Validate Telegram Login Widget data. Returns the Telegram user id or raises ValueError."""
    received = data.get("hash", "")
    fields = {k: v for k, v in data.items() if k != "hash"}
    if not received or "id" not in fields or "auth_date" not in fields:
        raise ValueError("missing fields")
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("bad signature")
    try:
        auth_date = int(fields["auth_date"])
        tg_id = int(fields["id"])
    except ValueError as exc:
        raise ValueError("bad fields") from exc
    if (now or time.time()) - auth_date > AUTH_MAX_AGE_SECONDS:
        raise ValueError("expired")
    return tg_id


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def is_admin(session: AsyncSession, user_id: int) -> bool:
    return (
        await session.scalar(
            select(UserRole.id).where(UserRole.user_id == user_id, UserRole.role == ROLE_ADMIN)
        )
    ) is not None


async def create_session(session: AsyncSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        AdminSession(
            token_hash=_hash(token),
            user_id=user.id,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=datetime.now(UTC) + SESSION_TTL,
        )
    )
    await session.flush()
    return token


async def revoke_session(session: AsyncSession, token: str) -> None:
    await session.execute(
        update(AdminSession)
        .where(AdminSession.token_hash == _hash(token))
        .values(revoked_at=datetime.now(UTC))
    )


@dataclass
class AdminContext:
    user: User
    csrf_token: str


async def load_admin(request: Request, session: AsyncSession) -> AdminContext | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    row = await session.scalar(
        select(AdminSession).where(
            AdminSession.token_hash == _hash(token),
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > datetime.now(UTC),
        )
    )
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.anonymized_at is not None or not await is_admin(session, user.id):
        return None  # role removed: the session stops working immediately
    row.last_seen_at = datetime.now(UTC)
    return AdminContext(user=user, csrf_token=row.csrf_token)


def check_csrf(ctx: AdminContext, submitted: str | None) -> None:
    if not submitted or not hmac.compare_digest(ctx.csrf_token, submitted):
        raise HTTPException(status_code=403, detail="CSRF check failed")
