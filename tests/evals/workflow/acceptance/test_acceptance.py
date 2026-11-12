"""Acceptance tests for SHOP-42. Kept outside the fixture: the agent never sees them; the scorer runs them."""
import pytest
from shop.orders import Order, checkout


def order(**lines) -> Order:
    o = Order()
    for sku, qty in lines.items():
        o.add(sku, qty)
    return o


def test_no_code_reports_a_zero_discount():
    assert checkout(order(TEE=1)) == {"subtotal_cents": 2500, "discount_cents": 0, "shipping_cents": 500,
                                      "total_cents": 3000}


def test_percentage_code_reduces_the_subtotal_only():
    res = checkout(order(TEE=1), code="WELCOME10")
    assert (res["discount_cents"], res["shipping_cents"], res["total_cents"]) == (250, 500, 2750)


def test_code_is_case_insensitive_and_trimmed():
    assert checkout(order(TEE=1), code="  welcome10 ")["discount_cents"] == 250


def test_unknown_code_is_an_error():
    with pytest.raises(ValueError):
        checkout(order(TEE=1), code="FREEBIE")


def test_shipping_is_decided_after_the_discount():
    # 2 x TEE = 5000 qualifies for free shipping, but not once 25% is taken off
    res = checkout(order(TEE=2), code="SUMMER25")
    assert (res["discount_cents"], res["shipping_cents"], res["total_cents"]) == (1250, 500, 4250)


def test_discount_is_rounded_up_to_a_whole_cent():
    # PIN costs 199: 10% is 19.9 cents and 25% is 49.75 cents
    assert checkout(order(PIN=1), code="WELCOME10")["discount_cents"] == 20
    assert checkout(order(PIN=1), code="SUMMER25")["discount_cents"] == 50
    assert checkout(order(PIN=1), code="SUMMER25")["total_cents"] == 199 - 50 + 500


def test_whole_cent_discounts_are_not_rounded():
    assert checkout(order(MUG=1), code="SUMMER25")["discount_cents"] == 300


def test_shipped_codes_exist():
    from shop.discounts import DISCOUNT_CODES
    assert DISCOUNT_CODES["WELCOME10"] == 10 and DISCOUNT_CODES["SUMMER25"] == 25


def test_empty_order_still_cannot_check_out():
    with pytest.raises(ValueError):
        checkout(Order(), code="WELCOME10")
