from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.integrations.thetender.client import HttpTenderClient, TenderError, parse_categories
from app.services import links
from app.services.search_params import (
    REGIONS,
    format_number,
    is_effective,
    normalize,
    parse_number,
    summary_lines,
    to_api,
)


def test_regions_list_matches_api_doc():
    assert len(REGIONS) == 27
    assert "Київ" in REGIONS and "Київська область" in REGIONS


def test_normalize_and_effective():
    assert not is_effective({})
    assert not is_effective({"city": "  ", "regions": []})
    # Area unit alone is not an effective filter.
    assert not is_effective({"area_unit": "ha."})
    assert is_effective({"min_price": "100"})
    assert normalize({"regions": ["Львівська область", "Київ", "Марс"]}) == {
        "regions": ["Київ", "Львівська область"]  # canonical order, unknown dropped
    }


def test_to_api_serializes_regions_comma_separated_and_full_set():
    params = {
        "auction_type": "sale",
        "regions": ["Київ", "Львівська область"],
        "city": "Біла Церква",
        "min_price": "1000",
        "area_unit": "sq.m.",
        "max_area": "50",
    }
    assert to_api(params) == {
        "auction_type": "sale",
        "region": "Київ,Львівська область",
        "city": "Біла Церква",
        "min_price": "1000",
        "area_unit": "sq.m.",
        "max_area": "50",
    }


def test_city_is_sent_unchanged():
    assert to_api({"city": "м. Київ, Дарницький р-н"})["city"] == "м. Київ, Дарницький р-н"


@pytest.mark.parametrize(
    ("text", "value"),
    [("150000", "150000"), ("1 000 000", "1000000"), ("2,5", "2.5"), ("99.99", "99.99")],
)
def test_parse_number(text, value):
    assert parse_number(text) == Decimal(value)


@pytest.mark.parametrize("text", ["", "abc", "-5", "1e9", "1,2,3", "9999999999999"])
def test_parse_number_rejects(text):
    with pytest.raises(ValueError):
        parse_number(text)


def test_format_and_summary():
    assert format_number("1234567") == "1 234 567"
    assert format_number("1234.5") == "1 234,50"
    lines = dict(summary_lines({"min_price": "1000", "area_unit": "ha.", "max_area": "3"}))
    assert lines["Ціна"] == "від 1 000 грн"
    assert lines["Площа"] == "до 3 га"


def test_parse_categories_shapes():
    assert parse_categories(["А", "Б", "А"]) == ["А", "Б"]
    assert parse_categories([{"id": 1, "name": "А"}, {"title": "Б"}]) == ["А", "Б"]
    assert parse_categories({"data": [{"name": "А"}]}) == ["А"]
    with pytest.raises(TenderError):
        parse_categories("oops")


def make_client(handler) -> HttpTenderClient:
    return HttpTenderClient(
        base_url="https://sandbox.mxuser.com",
        api_key="KEY123",
        transport=httpx.MockTransport(handler),
        retries=1,
    )


async def test_client_search_sends_auth_header_and_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "itemsCount": 75,
                "pagesCount": 2,
                "page": 1,
                "pagesize": 50,
                "data": [
                    {"name": "Лот", "number": "LSE001", "price": "1000.00", "url": "https://x"}
                ],
            },
        )

    client = make_client(handler)
    page = await client.search_auctions({"region": "Київ,Львівська область"}, page=1)
    await client.aclose()
    assert seen["auth"] == "KEY123"
    assert "KEY123" not in seen["url"]  # key never in the URL
    assert "pagesize=50" in seen["url"] and "page=1" in seen["url"]
    assert page.items[0].number == "LSE001"
    assert page.has_more and page.items_count == 75


@pytest.mark.parametrize(
    ("status", "kind"), [(403, "auth"), (404, "not_found"), (422, "validation")]
)
async def test_client_maps_errors(status, kind):
    client = make_client(lambda r: httpx.Response(status))
    with pytest.raises(TenderError) as exc:
        await client.search_auctions({"city": "x"})
    assert exc.value.kind == kind


async def test_client_retries_5xx_then_fails():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502)

    client = make_client(handler)
    with pytest.raises(TenderError) as exc:
        await client.get_categories()
    assert exc.value.kind == "unavailable" and len(calls) == 2


async def test_client_caches_categories():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=["Нерухомість"])

    client = make_client(handler)
    assert await client.get_categories() == ["Нерухомість"]
    assert await client.get_categories() == ["Нерухомість"]
    assert len(calls) == 1


def test_link_tokens_roundtrip_and_tamper():
    key = b"k" * 32
    token = links.make_token(
        key, 7, "https://sandbox.mxuser.com/a/1?utm_source=telegram", "SEARCH", "N1"
    )
    target = links.parse_token(key, token)
    assert (
        target
        and target.user_id == 7
        and target.source == "SEARCH"
        and target.auction_number == "N1"
    )
    body, sig = token.split(".")
    assert links.parse_token(key, body + "." + sig[:-1] + ("A" if sig[-1] != "A" else "B")) is None
    assert links.parse_token(b"other" * 8, token) is None
    assert links.parse_token(key, "garbage") is None


def test_destination_allowlist():
    hosts = links.allowed_hosts("https://sandbox.mxuser.com")
    assert links.is_allowed_destination("https://thetender.com.ua/auction/1", hosts)
    assert not links.is_allowed_destination("https://evil.example.com/", hosts)
    assert not links.is_allowed_destination("javascript:alert(1)", hosts)


def test_bot_user_agents():
    assert links.is_bot_user_agent("TelegramBot (like TwitterBot)")
    assert links.is_bot_user_agent(None)
    assert not links.is_bot_user_agent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)")


async def test_client_monitoring_crud_contract():
    """Monitoring calls follow API doc §6: JSON body, full replacement, no retry on create."""
    import json as jsonlib

    import httpx

    from app.integrations.thetender.client import HttpTenderClient, TenderError

    seen: list[tuple[str, str, dict | None]] = []
    stored = {"id": 15, "user_id": "777", "monitoring": {"name": "Київ", "region": "Київ"}}

    def handler(request: httpx.Request) -> httpx.Response:
        body = jsonlib.loads(request.content) if request.content else None
        seen.append((request.method, str(request.url), body))
        path = request.url.path
        if path == "/rest/monitoring" and request.method == "GET":
            return httpx.Response(200, json=[stored])
        if path == "/rest/monitoring/create":
            if body["name"] == "fail":
                return httpx.Response(503)
            return httpx.Response(200, json={"saved": True, "errors": [], "data": stored})
        if path == "/rest/monitoring/update":
            return httpx.Response(
                200, json={"saved": False, "errors": ["Невідомий регіон"], "data": None}
            )
        if path == "/rest/monitoring/delete":
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404)

    c = HttpTenderClient(
        base_url="https://thetender.com.ua", api_key="k", transport=httpx.MockTransport(handler)
    )
    (m,) = await c.list_monitorings("777")
    assert (m.id, m.name, m.params) == ("15", "Київ", {"regions": ["Київ"]})
    created = await c.create_monitoring("777", "Київ", {"regions": ["Київ"]})
    assert created.id == "15"
    method, url, body = seen[-1]
    assert method == "POST" and body["user_id"] == "777" and body["region"] == ["Київ"]
    assert body["keywords"] is None
    try:
        await c.update_monitoring("15", "777", "Київ", {"regions": ["Київ"]})
    except TenderError as exc:
        assert exc.kind == "validation" and exc.errors == ["Невідомий регіон"]
    else:
        raise AssertionError("expected validation error")
    assert seen[-1][0] == "PUT" and "id=15" in seen[-1][1]
    await c.delete_monitoring("15")
    assert seen[-1][0] == "DELETE" and "id=15" in seen[-1][1]

    before = len(seen)
    try:
        await c.create_monitoring("777", "fail", {"regions": ["Київ"]})
    except TenderError as exc:
        assert exc.kind == "ambiguous"
    assert len(seen) - before == 1  # a non-idempotent create is never retried
    await c.aclose()
