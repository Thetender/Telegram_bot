from __future__ import annotations

from typing import Any

from app.models import ROLE_MANAGER


async def registered(_: Any, user: Any = None) -> bool:
    return user is not None


async def is_manager(_: Any, roles: set[str] | None = None) -> bool:
    return bool(roles) and ROLE_MANAGER in roles
