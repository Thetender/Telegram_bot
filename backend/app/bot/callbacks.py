"""Callback data shared between handler modules (avoids circular imports)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MCb(CallbackData, prefix="m"):
    """Monitorings. a: list | show | res | edit | save | rename | cancel | del | delok |
    new (create from snapshot) | usename (use suggested name)."""

    a: str
    i: int = 0  # local monitoring id, or snapshot id for new/usename
    p: int = 0  # list page
