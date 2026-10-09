"""Search filter set shared by search drafts, snapshots and (later) monitorings.

Stored locally as a plain dict (JSONB). Regions are a list locally and are
serialized comma-separated for the REST API (API doc §5).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# Exact region names accepted by The Tender API (API doc §5).
REGIONS: tuple[str, ...] = (
    "Автономна Республіка Крим",
    "Вінницька область",
    "Волинська область",
    "Дніпропетровська область",
    "Донецька область",
    "Житомирська область",
    "Закарпатська область",
    "Запорізька область",
    "Івано-Франківська область",
    "Київ",
    "Київська область",
    "Кіровоградська область",
    "Луганська область",
    "Львівська область",
    "Миколаївська область",
    "Одеська область",
    "Полтавська область",
    "Рівненська область",
    "Севастополь",
    "Сумська область",
    "Тернопільська область",
    "Харківська область",
    "Херсонська область",
    "Хмельницька область",
    "Черкаська область",
    "Чернівецька область",
    "Чернігівська область",
)

AUCTION_TYPES = {"sale": "Продаж", "rent": "Оренда"}
PRICE_TYPES = {"eng": "Англійський", "hol": "Голландський"}
AREA_UNITS = {"ha.": "га", "sq.m.": "м²"}

TEXT_FIELDS = ("city", "keywords", "customer_name")
NUMBER_FIELDS = ("min_price", "max_price", "min_area", "max_area")
MAX_TEXT_LEN = 200
MAX_NUMBER = Decimal("1000000000000")

# API field order (API doc §4).
API_FIELDS = (
    "auction_type",
    "start_price_type",
    "category",
    "region",
    "city",
    "keywords",
    "customer_name",
    "min_price",
    "max_price",
    "area_unit",
    "min_area",
    "max_area",
)


def normalize(params: dict[str, Any] | None) -> dict[str, Any]:
    """Drop empty values and keep a canonical shape (used for comparisons)."""
    p = dict(params or {})
    out: dict[str, Any] = {}
    for key in ("auction_type", "start_price_type", "category", *TEXT_FIELDS):
        value = p.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    regions = [r for r in REGIONS if r in set(p.get("regions") or [])]
    if regions:
        out["regions"] = regions
    for key in NUMBER_FIELDS:
        if p.get(key) not in (None, ""):
            out[key] = str(p[key])
    # Area unit only matters when at least one area bound is set.
    if ("min_area" in out or "max_area" in out) and p.get("area_unit") in AREA_UNITS:
        out["area_unit"] = p["area_unit"]
    else:
        out.pop("min_area", None)
        out.pop("max_area", None)
    return out


def is_effective(params: dict[str, Any] | None) -> bool:
    return bool(normalize(params))


def to_api(params: dict[str, Any]) -> dict[str, str]:
    """Full parameter set for The Tender REST API."""
    p = normalize(params)
    out: dict[str, str] = {}
    for key in API_FIELDS:
        if key == "region":
            if p.get("regions"):
                out["region"] = ",".join(p["regions"])
        elif key in p:
            out[key] = str(p[key])
    return out


def parse_number(text: str) -> Decimal:
    """Parse '1 000 000', '1000,50', '1.5'. Raises ValueError."""
    cleaned = re.sub(r"[\s  ]", "", text or "").replace(",", ".")
    cleaned = cleaned.removesuffix("грн").removesuffix("га").removesuffix("м²")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
        raise ValueError("not a number")
    value = Decimal(cleaned)
    if value > MAX_NUMBER:
        raise ValueError("too large")
    return value


def format_number(value: Any) -> str:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if d == d.to_integral_value():
        s = f"{int(d):,}".replace(",", " ")
    else:
        s = f"{d:,.2f}".replace(",", " ").replace(".", ",")
    return s


def _range(lo: Any, hi: Any, suffix: str) -> str:
    if lo is not None and hi is not None:
        return f"від {format_number(lo)} до {format_number(hi)} {suffix}"
    if lo is not None:
        return f"від {format_number(lo)} {suffix}"
    return f"до {format_number(hi)} {suffix}"


def summary_lines(params: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(label, value) pairs for display, in screen order."""
    p = normalize(params)
    lines: list[tuple[str, str]] = []
    if "auction_type" in p:
        lines.append(("Продаж / Оренда", AUCTION_TYPES.get(p["auction_type"], p["auction_type"])))
    if "start_price_type" in p:
        lines.append(("Тип аукціону", PRICE_TYPES.get(p["start_price_type"], "")))
    if "category" in p:
        lines.append(("Категорія", p["category"]))
    if "regions" in p:
        lines.append(("Регіон", ", ".join(p["regions"])))
    if "city" in p:
        lines.append(("Місто", p["city"]))
    if "keywords" in p:
        lines.append(("Ключові слова", p["keywords"]))
    if "customer_name" in p:
        lines.append(("Організатор", p["customer_name"]))
    if "min_price" in p or "max_price" in p:
        lines.append(("Ціна", _range(p.get("min_price"), p.get("max_price"), "грн")))
    if "area_unit" in p:
        unit = AREA_UNITS[p["area_unit"]]
        lines.append(("Площа", _range(p.get("min_area"), p.get("max_area"), unit)))
    return lines
