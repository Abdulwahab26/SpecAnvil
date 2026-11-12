"""Discount codes: code -> percent off the subtotal."""
DISCOUNT_CODES = {"WELCOME10": 10, "SUMMER25": 25}


def discount_cents(subtotal_cents: int, code: str | None) -> int:
    """Discount for a code, rounded up to a whole cent. No code means no discount; an unknown code is an error."""
    if code is None:
        return 0
    key = code.strip().upper()
    if key not in DISCOUNT_CODES:
        raise ValueError(f"unknown discount code: {code.strip()}")
    return -(-subtotal_cents * DISCOUNT_CODES[key] // 100)
