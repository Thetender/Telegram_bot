"""Demo/test implementation of The Tender API with generated data.

Used on the test server (THETENDER_MOCK=true) until the sandbox key is
configured, and in automated tests. Never used in production.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.integrations.thetender.client import DEFAULT_PAGESIZE, Auction, AuctionPage, TenderError

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

    async def aclose(self) -> None:
        return None
