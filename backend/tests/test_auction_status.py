"""Status / auction-date lines (API change 22.10.2026)."""

from app.bot import texts as t
from app.bot.handlers.results import format_auction
from app.integrations.thetender import Auction
from app.services import auction_info, links


def test_status_lines():
    assert auction_info.status_line("Редагування доступне") == t.STATUS_EDITING
    assert auction_info.status_line("Прийняття заяв на участь") == t.STATUS_ACCEPTING
    assert auction_info.status_line("Щось нове") == "ℹ️ Щось нове"
    assert auction_info.status_line(None) is None


def test_auction_start_formats():
    assert auction_info.start_line(None) == t.NO_AUCTION_STAGE
    assert auction_info.start_line(auction_info.MISSING) is None
    assert auction_info.start_line("2026-11-12T10:00:00+02:00") == "📅 Торги: 12.11.2026"
    assert auction_info.start_line("2026-11-12 10:00:00") == "📅 Торги: 12.11.2026"
    assert auction_info.start_line("12.11.2026 10:00") == "📅 Торги: 12.11.2026"
    # 23:30 UTC is already the next day in Kyiv
    assert auction_info.start_line("2026-11-11T23:30:00Z") == "📅 Торги: 12.11.2026"


def test_result_block_shows_status_and_date():
    a = Auction.from_api(
        {"name": "Лот", "number": "X1", "url": "https://thetender.com.ua/a", "price": "100",
         "status": "Редагування доступне", "auctionStart": None}
    )  # fmt: skip
    text = format_auction(a, "https://x", 1)
    assert t.STATUS_EDITING in text and t.NO_AUCTION_STAGE in text
    # demo data without the fields: no extra lines
    plain = format_auction(Auction(name="Лот", number="1", url=""), "https://x", 1)
    assert "📅" not in plain and "🟢" not in plain


def test_notification_shows_status(monkeypatch):
    from types import SimpleNamespace

    from app.workers.delivery import render_for_watcher

    d = SimpleNamespace(
        auction={"name": "Лот", "price": "1", "url": "https://thetender.com.ua/a",
                 "status": "Прийняття заяв на участь", "auctionStart": "2026-11-12"},
        monitoring_name="М",
    )  # fmt: skip
    client = SimpleNamespace(name="К", phone="+380", username=None)
    text = render_for_watcher(d, client)
    assert "🟢 Приймаються заяви\n📅 Торги: 12.11.2026\n\n🔗" in text


def test_utm_source_follows_environment():
    from app.config import Settings

    prod = Settings(telegram_bot_token="x", environment="prod")
    assert prod.utm_source_value == "telegram_prod"
    assert Settings(telegram_bot_token="x", environment="test").utm_source_value == "telegram"
    try:
        links.configure_utm(prod.utm_source_value)
        url = links.with_utm("https://thetender.com.ua/a?utm_source=telegram_prod", links.LOT_ALERT)
        assert "utm_source=telegram_prod" in url
    finally:
        links.configure_utm("telegram")
