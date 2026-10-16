"""Managers/admins responsible for a client's monitoring (copies of notifications)."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Monitoring, MonitoringWatcher, User, UserRole
from app.services import events
from app.services.search_params import (
    AREA_UNITS,
    AUCTION_TYPES,
    NUMBER_FIELDS,
    PRICE_TYPES,
    REGIONS,
    TEXT_FIELDS,
    normalize,
    parse_number,
)


async def staff_users(session: AsyncSession) -> list[User]:
    """Everyone with a manager or admin role (who can be responsible)."""
    return list(
        await session.scalars(
            select(User)
            .where(
                User.anonymized_at.is_(None),
                User.id.in_(select(UserRole.user_id)),
            )
            .order_by(User.name, User.id)
        )
    )


async def creators(session: AsyncSession, monitorings: list[Monitoring]) -> dict[int, str]:
    """monitoring id → «Користувач» or «Адмін (Ім'я)» / «Менеджер (Ім'я)»."""
    ids = {m.created_by for m in monitorings if m.created_by}
    users = (
        {u.id: u for u in await session.scalars(select(User).where(User.id.in_(ids)))}
        if ids
        else {}
    )
    roles: dict[int, set[str]] = {}
    if ids:
        for uid, role in await session.execute(
            select(UserRole.user_id, UserRole.role).where(UserRole.user_id.in_(ids))
        ):
            roles.setdefault(uid, set()).add(role)
    out = {}
    for m in monitorings:
        staff = users.get(m.created_by) if m.created_by else None
        if staff is None:
            out[m.id] = "Користувач"
            continue
        who = staff.name or (f"@{staff.username}" if staff.username else "без імені")
        title = "Менеджер" if roles.get(staff.id) == {"MANAGER"} else "Адмін"
        out[m.id] = f"{title} ({who})"
    return out


async def watchers_by_monitoring(
    session: AsyncSession, monitoring_ids: list[int]
) -> dict[int, list[User]]:
    out: dict[int, list[User]] = {mid: [] for mid in monitoring_ids}
    if not monitoring_ids:
        return out
    rows = await session.execute(
        select(MonitoringWatcher.monitoring_id, User)
        .join(User, User.id == MonitoringWatcher.user_id)
        .where(MonitoringWatcher.monitoring_id.in_(monitoring_ids))
        .order_by(User.name)
    )
    for mid, user in rows:
        out[mid].append(user)
    return out


async def add(session: AsyncSession, monitoring: Monitoring, staff_id: int, admin: User) -> bool:
    if staff_id not in {u.id for u in await staff_users(session)}:
        return False
    await session.execute(
        insert(MonitoringWatcher)
        .values(monitoring_id=monitoring.id, user_id=staff_id, added_by=admin.id)
        .on_conflict_do_nothing(constraint="uq_monitoring_watcher")
    )
    await events.log_event(
        session,
        events.SETTINGS_CHANGED,
        user_id=monitoring.user_id,
        actor_user_id=admin.id,
        data={"watcher_added": staff_id, "monitoring_id": monitoring.id},
    )
    return True


async def remove(session: AsyncSession, monitoring: Monitoring, staff_id: int, admin: User) -> None:
    await session.execute(
        delete(MonitoringWatcher).where(
            MonitoringWatcher.monitoring_id == monitoring.id, MonitoringWatcher.user_id == staff_id
        )
    )
    await events.log_event(
        session,
        events.SETTINGS_CHANGED,
        user_id=monitoring.user_id,
        actor_user_id=admin.id,
        data={"watcher_removed": staff_id, "monitoring_id": monitoring.id},
    )


def params_from_form(form: dict[str, list[str]]) -> tuple[dict, list[str]]:
    """Monitoring parameters from the Dashboard form → (params, errors)."""
    first = {k: (v[0].strip() if v else "") for k, v in form.items()}
    params: dict = {}
    errors: list[str] = []
    if first.get("auction_type") in AUCTION_TYPES:
        params["auction_type"] = first["auction_type"]
    if first.get("start_price_type") in PRICE_TYPES:
        params["start_price_type"] = first["start_price_type"]
    if first.get("category"):
        params["category"] = first["category"][:200]
    params["regions"] = [r for r in form.get("regions", []) if r in REGIONS]
    for key in TEXT_FIELDS:
        if first.get(key):
            params[key] = first[key][: 255 if key == "balance_holder" else 200]
    labels = {
        "min_price": "Ціна від",
        "max_price": "Ціна до",
        "min_area": "Площа від",
        "max_area": "Площа до",
    }
    for key in NUMBER_FIELDS:
        if first.get(key):
            try:
                params[key] = str(parse_number(first[key]))
            except ValueError:
                errors.append(f"«{labels[key]}»: введіть число")
    if first.get("area_unit") in AREA_UNITS:
        params["area_unit"] = first["area_unit"]
    params = normalize(params)
    if not params and not errors:
        errors.append("Вкажіть хоча б один параметр")
    return params, errors
