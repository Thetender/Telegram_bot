"""Client for The Tender REST API (Telegram key).

See docs/thetender_api_telegram.txt. Authorization is sent in the
Authorization header; the key never appears in URLs or logs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

DEFAULT_PAGESIZE = 50


class TenderError(Exception):
    """API failure. kind: auth | not_found | validation | unavailable | bad_response."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind


@dataclass(frozen=True)
class Auction:
    name: str
    number: str
    url: str
    price: Any = None
    company_name: str | None = None
    auction_start: str | None = None
    status: str | None = None
    start_price_type: str | None = None
    auction_type: str | None = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Auction:
        return cls(
            name=str(d.get("name") or "").strip() or "Без назви",
            number=str(d.get("number") or ""),
            url=str(d.get("url") or ""),
            price=d.get("price"),
            company_name=d.get("companyName"),
            auction_start=d.get("auctionStart"),
            status=d.get("status"),
            start_price_type=d.get("start_price_type"),
            auction_type=d.get("auction_type"),
        )


@dataclass(frozen=True)
class AuctionPage:
    items: list[Auction]
    page: int
    pages_count: int
    items_count: int

    @property
    def has_more(self) -> bool:
        return self.page < self.pages_count


class TenderClient(Protocol):
    is_mock: bool

    async def get_categories(self) -> list[str]: ...

    async def search_auctions(
        self, params: dict[str, str], page: int = 1, pagesize: int = DEFAULT_PAGESIZE
    ) -> AuctionPage: ...

    async def aclose(self) -> None: ...


def parse_categories(payload: Any) -> list[str]:
    """Tolerant parser: list of names, list of objects, or wrapped in 'data'."""
    if isinstance(payload, dict):
        for key in ("data", "categories", "items"):
            if key in payload:
                return parse_categories(payload[key])
        raise TenderError("bad_response", "unexpected categories object")
    if not isinstance(payload, list):
        raise TenderError("bad_response", "categories is not a list")
    names: list[str] = []
    for item in payload:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title") or ""
        else:
            continue
        name = str(name).strip()
        if name and name not in names:
            names.append(name)
    return names


def parse_page(payload: Any, requested_page: int) -> AuctionPage:
    if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
        raise TenderError("bad_response", "unexpected auctions response")
    items = [Auction.from_api(d) for d in payload.get("data") or [] if isinstance(d, dict)]
    try:
        page = int(payload.get("page") or requested_page)
        pages = int(payload.get("pagesCount") or 0)
        count = int(payload.get("itemsCount") or len(items))
    except (TypeError, ValueError) as exc:
        raise TenderError("bad_response", "bad pagination fields") from exc
    if not items:
        pages = min(pages, page)
    return AuctionPage(items=items, page=page, pages_count=pages, items_count=count)


@dataclass
class HttpTenderClient:
    base_url: str
    api_key: str
    timeout: float = 15.0
    retries: int = 2
    categories_ttl: float = 600.0
    transport: httpx.AsyncBaseTransport | None = None
    is_mock: bool = False
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _categories: tuple[float, list[str]] | None = field(default=None, init=False, repr=False)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
                transport=self.transport,
            )
        return self._client

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = await self._http().get(path, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "The Tender GET %s network error (attempt %s): %s",
                    path,
                    attempt + 1,
                    type(exc).__name__,
                )
            else:
                if resp.status_code == 403:
                    raise TenderError("auth", "API key rejected (403)")
                if resp.status_code == 404:
                    raise TenderError("not_found")
                if 400 <= resp.status_code < 500:
                    raise TenderError("validation", f"HTTP {resp.status_code}")
                if resp.status_code >= 500:
                    last_exc = TenderError("unavailable", f"HTTP {resp.status_code}")
                    log.warning(
                        "The Tender GET %s -> %s (attempt %s)", path, resp.status_code, attempt + 1
                    )
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise TenderError("bad_response", "invalid JSON") from exc
            if attempt < self.retries:
                await asyncio.sleep(0.5 * (attempt + 1))
        raise TenderError("unavailable", str(last_exc) if last_exc else "")

    async def get_categories(self) -> list[str]:
        now = time.monotonic()
        if self._categories and now - self._categories[0] < self.categories_ttl:
            return self._categories[1]
        names = parse_categories(await self._get("/rest/categories"))
        self._categories = (now, names)
        return names

    async def search_auctions(
        self, params: dict[str, str], page: int = 1, pagesize: int = DEFAULT_PAGESIZE
    ) -> AuctionPage:
        query = {**params, "page": page, "pagesize": pagesize}
        return parse_page(await self._get("/rest/auctions", query), page)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
