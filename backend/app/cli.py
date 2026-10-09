"""Operational CLI (run inside the app container).

    python -m app.cli grant-admin --phone +380671234567
    python -m app.cli grant-admin --telegram-id 123456789
    python -m app.cli publish-legal --type TERMS --version 1.0 \\
        --url https://thetender.com.ua/terms --stored-ref terms-v1.0.pdf
    python -m app.cli grant-manager --phone +380671234567
    python -m app.cli list-admins
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import ROLE_ADMIN, ROLE_MANAGER, User, UserRole
from app.services import legal
from app.services import users as users_svc
from app.services.phone import normalize_phone


async def grant_admin(telegram_id: int | None, phone: str | None, role: str = ROLE_ADMIN) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    try:
        async with sf() as session:
            if telegram_id is not None:
                user = await users_svc.get_by_telegram_id(session, telegram_id)
            else:
                assert phone is not None
                user = await session.scalar(
                    select(User).where(User.phone == normalize_phone(phone))
                )
            who = f"Telegram ID {telegram_id}" if telegram_id is not None else f"phone {phone}"
            if user is None or user.anonymized_at is not None:
                print(
                    f"User with {who} is not registered. "
                    "Ask them to open the bot and share their phone first.",
                    file=sys.stderr,
                )
                return 1
            granted = await users_svc.grant_role(session, user, role, actor_user_id=None)
            await session.commit()
            print(f"{role} role granted." if granted else f"User is already {role}.")
            return 0
    finally:
        await engine.dispose()


async def list_admins() -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    try:
        async with sf() as session:
            rows = await session.execute(
                select(User.telegram_id, User.name, User.username)
                .join(UserRole, UserRole.user_id == User.id)
                .where(UserRole.role == ROLE_ADMIN)
            )
            for tid, name, username in rows:
                print(f"{tid}\t{name or ''}\t@{username or ''}")
            return 0
    finally:
        await engine.dispose()


async def publish_legal(
    doc_type: str, version: str, url: str, stored_ref: str, reaccept: bool, effective: str | None
) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    effective_at = datetime.fromisoformat(effective) if effective else datetime.now(UTC)
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=UTC)
    try:
        async with sf() as session:
            try:
                v = await legal.publish_version(
                    session,
                    doc_type=doc_type,
                    version=version,
                    public_url=url,
                    stored_reference=stored_ref,
                    effective_at=effective_at,
                    require_reacceptance=reaccept,
                )
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            await session.commit()
            print(f"Published {doc_type} v{v.version} (id={v.id}).")
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("grant-admin", help="Grant ADMIN to a registered bot user")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--telegram-id", type=int)
    who.add_argument("--phone", help="Phone the user registered with, e.g. +380671234567")

    p = sub.add_parser("grant-manager", help="Grant MANAGER to a registered bot user")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--telegram-id", type=int)
    who.add_argument("--phone")

    sub.add_parser("list-admins", help="List admins")

    p = sub.add_parser("publish-legal", help="Publish a new immutable legal document version")
    p.add_argument("--type", choices=[legal.TERMS, legal.PRIVACY], required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--url", required=True, help="Public URL shown to users")
    p.add_argument("--stored-ref", required=True, help="Immutable stored copy reference")
    p.add_argument("--require-reacceptance", action="store_true")
    p.add_argument("--effective", help="ISO date/time, default now")

    args = parser.parse_args(argv)
    if args.cmd == "grant-admin":
        return asyncio.run(grant_admin(args.telegram_id, args.phone))
    if args.cmd == "grant-manager":
        return asyncio.run(grant_admin(args.telegram_id, args.phone, role=ROLE_MANAGER))
    if args.cmd == "list-admins":
        return asyncio.run(list_admins())
    if args.cmd == "publish-legal":
        return asyncio.run(
            publish_legal(
                args.type,
                args.version,
                args.url,
                args.stored_ref,
                args.require_reacceptance,
                args.effective,
            )
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
