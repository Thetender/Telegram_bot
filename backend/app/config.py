"""Application settings, read from environment variables.

All secrets (bot token, API keys, DB password) come only from the environment
(.env file on the server). Nothing secret is stored in Git or in DB tables.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


class Settings(BaseModel):
    environment: Literal["local", "test", "prod"] = "local"

    # Telegram
    telegram_bot_token: SecretStr
    # "polling" for local development, "webhook" on the server.
    bot_mode: Literal["polling", "webhook"] = "polling"
    # Public HTTPS base URL of this service, e.g. https://tg-test.thetender.com.ua
    public_base_url: str | None = None
    # Secret that Telegram sends back in X-Telegram-Bot-Api-Secret-Token header.
    telegram_webhook_secret: SecretStr | None = None

    # Database
    database_url: str = "postgresql+psycopg://postgres@127.0.0.1:5432/thetender_tg"

    # The Tender REST API (used from Iteration 2)
    thetender_base_url: str = "https://sandbox.mxuser.com"
    thetender_api_key: SecretStr | None = None
    # Demo mode: built-in fake auction data, for testing the bot UX before the
    # sandbox API key is configured. Never allowed in prod.
    thetender_mock: bool = False
    # Secret for signing tracked auction links. Defaults to a key derived from
    # TELEGRAM_WEBHOOK_SECRET, so no extra setup is needed on the server.
    link_signing_secret: SecretStr | None = None

    # Business settings
    consultation_phone: str = "+38 067 333 78 00"
    timezone: str = "Europe/Kyiv"

    log_level: str = "INFO"

    @field_validator("public_base_url")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @property
    def telegram_webhook_path(self) -> str:
        return "/telegram/webhook"

    @property
    def link_signing_key(self) -> bytes | None:
        if self.link_signing_secret:
            return self.link_signing_secret.get_secret_value().encode()
        if self.telegram_webhook_secret:
            seed = "tracked-links:" + self.telegram_webhook_secret.get_secret_value()
            return hashlib.sha256(seed.encode()).digest()
        return None

    @property
    def tracked_links_enabled(self) -> bool:
        return bool(self.public_base_url and self.link_signing_key)

    def validate_runtime(self) -> None:
        """Fail fast on dangerous misconfiguration."""
        if self.bot_mode == "webhook":
            if not self.public_base_url:
                raise ValueError("PUBLIC_BASE_URL is required in webhook mode")
            if not self.telegram_webhook_secret:
                raise ValueError("TELEGRAM_WEBHOOK_SECRET is required in webhook mode")
        if self.environment == "prod" and self.thetender_mock:
            raise ValueError("THETENDER_MOCK must not be enabled in production")
        if self.environment != "prod" and "thetender.com.ua" in self.thetender_base_url:
            raise ValueError(
                "Non-production environment must not use the production The Tender API"
            )


def load_settings() -> Settings:
    data: dict[str, object] = {
        "environment": _env("ENVIRONMENT", "local"),
        "telegram_bot_token": _env("TELEGRAM_BOT_TOKEN", ""),
        "bot_mode": _env("BOT_MODE", "polling"),
        "public_base_url": _env("PUBLIC_BASE_URL"),
        "telegram_webhook_secret": _env("TELEGRAM_WEBHOOK_SECRET"),
        "database_url": _env(
            "DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/thetender_tg"
        ),
        "thetender_base_url": _env("THETENDER_BASE_URL", "https://sandbox.mxuser.com"),
        "thetender_api_key": _env("THETENDER_API_KEY"),
        "thetender_mock": _env("THETENDER_MOCK", "false"),
        "link_signing_secret": _env("LINK_SIGNING_SECRET"),
        "consultation_phone": _env("CONSULTATION_PHONE", "+38 067 333 78 00"),
        "timezone": _env("TIMEZONE", "Europe/Kyiv"),
        "log_level": _env("LOG_LEVEL", "INFO"),
    }
    return Settings.model_validate({k: v for k, v in data.items() if v is not None})


@lru_cache
def get_settings() -> Settings:
    return load_settings()


__all__ = ["Settings", "get_settings", "load_settings"]
