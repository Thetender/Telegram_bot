from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = [
    # Telegram bot token: 123456789:AA... (also inside api.telegram.org/bot<token>/ URLs)
    re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"),
]


class RedactSecretsFilter(logging.Filter):
    """Last line of defence: never let a bot token reach the logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = msg
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("***", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = None
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_tt_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecretsFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    root._tt_configured = True  # type: ignore[attr-defined]
