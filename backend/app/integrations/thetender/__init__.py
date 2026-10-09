from __future__ import annotations

import logging

from app.config import Settings
from app.integrations.thetender.client import (
    DEFAULT_PAGESIZE,
    Auction,
    AuctionPage,
    HttpTenderClient,
    TenderClient,
    TenderError,
)
from app.integrations.thetender.mock import MockTenderClient

log = logging.getLogger(__name__)


def make_tender_client(settings: Settings) -> TenderClient:
    if settings.thetender_mock:
        log.warning("The Tender API: DEMO mode (generated data), THETENDER_MOCK=true")
        return MockTenderClient()
    if settings.environment != "prod" and "thetender.com.ua" in settings.thetender_base_url:
        log.warning("Test environment is using the PRODUCTION The Tender API (explicitly allowed)")
    key = settings.thetender_api_key.get_secret_value() if settings.thetender_api_key else ""
    if not key:
        log.warning("THETENDER_API_KEY is not set: search will report the API as unavailable")
    return HttpTenderClient(base_url=settings.thetender_base_url, api_key=key)


__all__ = [
    "DEFAULT_PAGESIZE",
    "Auction",
    "AuctionPage",
    "HttpTenderClient",
    "MockTenderClient",
    "TenderClient",
    "TenderError",
    "make_tender_client",
]
