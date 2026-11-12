"""Order totals."""
from dataclasses import dataclass, field

from .catalog import get_product

SHIPPING_CENTS = 500
FREE_SHIPPING_FROM_CENTS = 5000


@dataclass
class Order:
    lines: dict[str, int] = field(default_factory=dict)  # sku -> quantity

    def add(self, sku: str, quantity: int = 1) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        get_product(sku)
        self.lines[sku] = self.lines.get(sku, 0) + quantity

    def subtotal_cents(self) -> int:
        return sum(get_product(sku).price_cents * qty for sku, qty in self.lines.items())


def shipping_cents(subtotal_cents: int) -> int:
    """Flat shipping, free from FREE_SHIPPING_FROM_CENTS."""
    if subtotal_cents == 0 or subtotal_cents >= FREE_SHIPPING_FROM_CENTS:
        return 0
    return SHIPPING_CENTS


def checkout(order: Order) -> dict:
    """Totals for an order: subtotal, shipping and the amount to pay, all in cents."""
    if not order.lines:
        raise ValueError("cannot check out an empty order")
    subtotal = order.subtotal_cents()
    shipping = shipping_cents(subtotal)
    return {"subtotal_cents": subtotal, "shipping_cents": shipping, "total_cents": subtotal + shipping}
