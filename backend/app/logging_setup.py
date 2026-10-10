from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = [
    # Telegram bot token: 123456789:AA... (also inside api.telegram.org/bot<token>/ URLs)
    re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"),
]
# The Tender sends its webhook as ...?auth=<API key>: hide the value.
_AUTH_QUERY = re.compile(r"([?&]auth=)[^&\s\"']+")


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return _AUTH_QUERY.sub(r"\1***", text)


class RedactSecretsFilter(logging.Filter):
    """Last line of defence: never let a bot token or API key reach the logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact string arguments in place (keeps the args tuple that
        # formatters such as uvicorn's access log rely on).
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = redact(msg)
        if redacted != msg:  # secret hidden inside a non-string argument
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
    # uvicorn's access log has its own handler: filter it too (request line
    # of the The Tender webhook contains ?auth=<key>).
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(name).addFilter(RedactSecretsFilter())
