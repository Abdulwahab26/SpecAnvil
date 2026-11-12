import pytest
from shop.orders import Order, checkout, shipping_cents


def test_subtotal_adds_lines():
    order = Order()
    order.add("MUG", 2)
    order.add("CAP")
    assert order.subtotal_cents() == 4200


def test_unknown_product_is_rejected():
    with pytest.raises(ValueError):
        Order().add("NOPE")


def test_shipping_is_free_from_the_threshold():
    assert shipping_cents(4999) == 500
    assert shipping_cents(5000) == 0


def test_checkout_totals():
    order = Order()
    order.add("TEE")
    res = checkout(order)
    assert (res["subtotal_cents"], res["shipping_cents"], res["total_cents"]) == (2500, 500, 3000)


def test_empty_order_cannot_check_out():
    with pytest.raises(ValueError):
        checkout(Order())
