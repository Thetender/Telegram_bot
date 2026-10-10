"""Demo/test implementation of The Tender API with generated data.

Used on the test server (THETENDER_MOCK=true) until the sandbox key is
configured, and in automated tests. Never used in production.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.integrations.thetender.client import (
    DEFAULT_PAGESIZE,
    Auction,
    AuctionPage,
    ExternalMonitoring,
    TenderError,
    parse_monitoring,
)
from app.services.search_params import to_monitoring_body

DEMO_CATEGORIES = [
    "Нерухомість",
    "Земельні ділянки",
    "Транспорт",
    "Обладнання",
    "Право вимоги",
    "Оренда державного майна",
    "Мала приватизація",
    "Інше майно",
]

_KINDS = ["Нежитлове приміщення", "Земельна ділянка", "Автомобіль", "Верстат", "Склад"]
_CITIES = ["Київ", "Львів", "Одеса", "Дніпро", "Харків"]


@dataclass
class MockTenderClient:
    """Deterministic fake. Total results depend on the filters:
    keywords containing 'нічого' -> 0 results; otherwise 120 base results,
    reduced by each filter. Set `fail_with` to simulate errors."""

    categories: list[str] = field(default_factory=lambda: list(DEMO_CATEGORIES))
    fail_with: str | None = None
    # e.g. {"create": "unavailable"} or {"create_ambiguous": "1"}
    fail_monitoring: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    is_mock: bool = True

    async def get_categories(self) -> list[str]:
        self.calls.append(("categories", {}))
        if self.fail_with:
            raise TenderError(self.fail_with)
        return list(self.categories)

    def _total(self, params: dict[str, str]) -> int:
        if "нічого" in params.get("keywords", "").lower():
            return 0
        total = 120
        for _ in [k for k in params if k not in ("page", "pagesize")]:
            total = max(3, total // 2)
        return total

    async def search_auctions(
        self, params: dict[str, str], page: int = 1, pagesize: int = DEFAULT_PAGESIZE
    ) -> AuctionPage:
        self.calls.append(("auctions", {**params, "page": page, "pagesize": pagesize}))
        if self.fail_with:
            raise TenderError(self.fail_with)
        total = self._total(params)
        pages = math.ceil(total / pagesize) if total else 0
        start = (page - 1) * pagesize
        items = []
        for i in range(start, min(start + pagesize, total)):
            n = i + 1
            kind = _KINDS[i % len(_KINDS)]
            city = params.get("city") or _CITIES[i % len(_CITIES)]
            items.append(
                Auction(
                    name=f"[ДЕМО] {kind}, м. {city}, лот №{n}",
                    number=f"LSE-DEMO-{n:05d}",
                    url=f"https://sandbox.mxuser.com/auction/LSE-DEMO-{n:05d}?utm_source=telegram",
                    price=f"{(n * 37_500) % 2_000_000 + 50_000}.00",
                    auction_type=params.get("auction_type", "sale"),
                    start_price_type=params.get("start_price_type", "eng"),
                )
            )
        return AuctionPage(items=items, page=page, pages_count=pages, items_count=total)

    # --- monitorings (in memory; same semantics as the real API) ---

    def _ensure_store(self) -> dict[str, dict]:
        if not hasattr(self, "_monitorings"):
            self._monitorings: dict[str, dict] = {}
            self._next_id = 100
        return self._monitorings

    def _check(self, kind: str) -> None:
        if self.fail_with:
            raise TenderError(self.fail_with)
        if kind in self.fail_monitoring:
            raise TenderError(self.fail_monitoring[kind])

    async def list_monitorings(self, user_id: str) -> list[ExternalMonitoring]:
        self.calls.append(("monitoring_list", {"user_id": user_id}))
        self._check("list")
        return [
            parse_monitoring(d) for d in self._ensure_store().values() if d["user_id"] == user_id
        ]

    def _validate(self, user_id: str, name: str, body: dict) -> None:
        if not name:
            raise TenderError("validation", "name", errors=["Вкажіть назву моніторингу"])
        if not any(v not in (None, "", []) for v in body.values()):
            raise TenderError("validation", "filters", errors=["Потрібен хоча б один фільтр"])

    async def create_monitoring(
        self, user_id: str, name: str, params: dict[str, Any]
    ) -> ExternalMonitoring:
        body = to_monitoring_body(params)
        self.calls.append(("monitoring_create", {"user_id": user_id, "name": name, **body}))
        self._check("create")
        self._validate(user_id, name, body)
        store = self._ensure_store()
        if sum(1 for d in store.values() if d["user_id"] == user_id) >= 200:
            raise TenderError("validation", "limit", errors=["Досягнуто ліміту 200 моніторингів"])
        self._next_id += 1
        stored = self._stored(str(self._next_id), user_id, name, body)
        store[str(stored["id"])] = stored
        if "create_ambiguous" in self.fail_monitoring:
            # Saved on the backend, but the response was lost.
            raise TenderError("ambiguous", "timeout")
        return parse_monitoring(stored)

    async def update_monitoring(
        self, monitoring_id: str, user_id: str, name: str, params: dict[str, Any]
    ) -> ExternalMonitoring:
        body = to_monitoring_body(params)
        self.calls.append(("monitoring_update", {"id": monitoring_id, "name": name, **body}))
        self._check("update")
        store = self._ensure_store()
        if monitoring_id not in store or store[monitoring_id]["user_id"] != user_id:
            raise TenderError("not_found")
        self._validate(user_id, name, body)
        store[monitoring_id] = self._stored(monitoring_id, user_id, name, body)
        return parse_monitoring(store[monitoring_id])

    async def delete_monitoring(self, monitoring_id: str) -> None:
        self.calls.append(("monitoring_delete", {"id": monitoring_id}))
        self._check("delete")
        if self._ensure_store().pop(monitoring_id, None) is None:
            raise TenderError("not_found")

    @staticmethod
    def _stored(monitoring_id: str, user_id: str, name: str, body: dict) -> dict:
        monitoring = {"name": name, **body}
        if isinstance(monitoring.get("region"), list):
            # The real API returns regions as a comma-separated string.
            monitoring["region"] = ",".join(monitoring["region"])
        return {"id": int(monitoring_id), "user_id": user_id, "monitoring": monitoring}

    async def aclose(self) -> None:
        return None
