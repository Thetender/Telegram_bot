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
    """API failure. kind: auth | not_found | validation | unavailable | bad_response |
    ambiguous (a non-idempotent call may or may not have been applied)."""

    def __init__(self, kind: str, message: str = "", errors: list[str] | None = None) -> None:
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind
        self.errors = errors or []


@dataclass(frozen=True)
class ExternalMonitoring:
    """A monitoring as stored by The Tender."""

    id: str
    name: str
    params: dict[str, Any]
    raw: dict[str, Any]


def parse_monitoring(d: Any) -> ExternalMonitoring:
    from app.services.search_params import from_api

    if not isinstance(d, dict) or d.get("id") in (None, ""):
        raise TenderError("bad_response", "monitoring without id")
    body = d.get("monitoring") if isinstance(d.get("monitoring"), dict) else d
    return ExternalMonitoring(
        id=str(d["id"]),
        name=str(body.get("name") or "").strip() or "Моніторинг",
        params=from_api(body),
        raw=d,
    )


def parse_monitoring_list(payload: Any) -> list[ExternalMonitoring]:
    if isinstance(payload, dict):
        for key in ("data", "monitorings", "items"):
            if key in payload:
                return parse_monitoring_list(payload[key])
        raise TenderError("bad_response", "unexpected monitoring list object")
    if not isinstance(payload, list):
        raise TenderError("bad_response", "monitoring list is not a list")
    return [parse_monitoring(d) for d in payload if isinstance(d, dict)]


def parse_saved(payload: Any) -> ExternalMonitoring:
    """Create/update response: {saved, errors, data}."""
    if not isinstance(payload, dict):
        raise TenderError("bad_response", "unexpected save response")
    if not payload.get("saved"):
        errors = [str(e) for e in (payload.get("errors") or [])]
        if isinstance(payload.get("errors"), dict):
            errors = [str(v) for v in payload["errors"].values()]
        raise TenderError("validation", "; ".join(errors) or "not saved", errors=errors)
    return parse_monitoring(payload.get("data"))


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

    async def list_monitorings(self, user_id: str) -> list[ExternalMonitoring]: ...

    async def create_monitoring(
        self, user_id: str, name: str, params: dict[str, Any]
    ) -> ExternalMonitoring: ...

    async def update_monitoring(
        self, monitoring_id: str, user_id: str, name: str, params: dict[str, Any]
    ) -> ExternalMonitoring: ...

    async def delete_monitoring(self, monitoring_id: str) -> None: ...

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


def _errors_from(resp: httpx.Response) -> list[str]:
    """Human-readable errors from a 4xx body ({"errors": [...]}) if present."""
    try:
        body = resp.json()
    except ValueError:
        return []
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, dict):
        errors = list(errors.values())
    if isinstance(errors, list):
        return [str(e) for e in errors if e not in (None, "")]
    return []


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
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
        retry: bool = True,
    ) -> Any:
        """retry=False for non-idempotent calls (monitoring create): a network
        error or 5xx then raises 'ambiguous' instead of repeating the call."""
        attempts = self.retries + 1 if retry else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._http().request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "The Tender %s %s network error (attempt %s): %s",
                    method,
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
                    raise TenderError(
                        "validation", f"HTTP {resp.status_code}", errors=_errors_from(resp)
                    )
                if resp.status_code >= 500:
                    last_exc = TenderError("unavailable", f"HTTP {resp.status_code}")
                    log.warning(
                        "The Tender %s %s -> %s (attempt %s)",
                        method,
                        path,
                        resp.status_code,
                        attempt + 1,
                    )
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise TenderError("bad_response", "invalid JSON") from exc
            if attempt < attempts - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
        kind = "unavailable" if retry else "ambiguous"
        raise TenderError(kind, str(last_exc) if last_exc else "")

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

    async def list_monitorings(self, user_id: str) -> list[ExternalMonitoring]:
        return parse_monitoring_list(await self._get("/rest/monitoring", {"user_id": user_id}))

    async def create_monitoring(
        self, user_id: str, name: str, params: dict[str, Any]
    ) -> ExternalMonitoring:
        from app.services.search_params import to_monitoring_body

        body = {"user_id": user_id, "name": name, **to_monitoring_body(params)}
        payload = await self._request("POST", "/rest/monitoring/create", json=body, retry=False)
        return parse_saved(payload)

    async def update_monitoring(
        self, monitoring_id: str, user_id: str, name: str, params: dict[str, Any]
    ) -> ExternalMonitoring:
        from app.services.search_params import to_monitoring_body

        body = {"user_id": user_id, "name": name, **to_monitoring_body(params)}
        payload = await self._request(
            "PUT", "/rest/monitoring/update", params={"id": monitoring_id}, json=body
        )
        return parse_saved(payload)

    async def delete_monitoring(self, monitoring_id: str) -> None:
        payload = await self._request(
            "DELETE", "/rest/monitoring/delete", params={"id": monitoring_id}
        )
        if isinstance(payload, dict) and payload.get("deleted") is False:
            raise TenderError("validation", "not deleted")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
