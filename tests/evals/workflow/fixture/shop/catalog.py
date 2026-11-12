"""Products and their prices, in cents."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int


PRODUCTS = {
    "MUG": Product("MUG", "Coffee mug", 1200),
    "TEE": Product("TEE", "T-shirt", 2500),
    "CAP": Product("CAP", "Cap", 1800),
    "PIN": Product("PIN", "Enamel pin", 199),
}


def get_product(sku: str) -> Product:
    """Look up a product; unknown SKUs are an error, not a silent zero."""
    try:
        return PRODUCTS[sku]
    except KeyError:
        raise ValueError(f"unknown product: {sku}") from None
