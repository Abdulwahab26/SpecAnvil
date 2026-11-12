import pytest
from shop.discounts import discount_cents
from shop.orders import Order, checkout


def test_discount_rounds_up():
    assert discount_cents(199, "WELCOME10") == 20


def test_unknown_code():
    with pytest.raises(ValueError):
        discount_cents(1000, "NOPE")


def test_checkout_with_code():
    order = Order()
    order.add("TEE")
    assert checkout(order, code="welcome10")["total_cents"] == 2750
