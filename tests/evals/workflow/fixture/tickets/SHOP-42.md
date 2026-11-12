# SHOP-42: Discount codes at checkout

**Type:** Story · **Status:** Ready

As a customer I want to enter a discount code at checkout so that I pay less.

## Details
- Codes live in a new module `shop/discounts.py` as `DISCOUNT_CODES`, a mapping from code to percent off.
  Ship with `WELCOME10` (10%) and `SUMMER25` (25%).
- `checkout(order, code=None)` accepts an optional code.
- The discount applies to the subtotal only, never to shipping.
- Shipping is decided on the subtotal **after** the discount.
- A discount that is not a whole number of cents is rounded **up** to the next cent (in the customer's favour).
- Codes are case-insensitive and surrounding spaces are ignored.
- An unknown code raises `ValueError`; it must not be ignored.
- The result gains `discount_cents` (0 when no code is given). `total_cents` is subtotal minus discount plus shipping.

## Out of scope
Expiry dates, one-time codes, stacking several codes.
