from __future__ import annotations

import re


def normalize_phone(raw: str) -> str:
    """Normalize a phone number shared via Telegram to E.164 (+380...).

    Telegram usually sends digits without '+', e.g. '380671234567'.
    A local Ukrainian form '0671234567' is converted to '+380671234567'.
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise ValueError("empty phone number")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if len(digits) < 8 or len(digits) > 15:
        raise ValueError(f"unexpected phone length: {len(digits)}")
    return "+" + digits
