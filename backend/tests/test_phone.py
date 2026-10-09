import pytest

from app.services.phone import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("380671234567", "+380671234567"),
        ("+380671234567", "+380671234567"),
        ("+38 (067) 123-45-67", "+380671234567"),
        ("0671234567", "+380671234567"),
        ("48123456789", "+48123456789"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "123", "1234567890123456"])
def test_normalize_phone_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)
