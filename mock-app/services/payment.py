"""Payment processing service."""

from decimal import Decimal


def calculate_discount(price: float, discount_pct: float) -> float:
    """Calculate discounted price. discount_pct should be 0-100."""
    if discount_pct >= 100:
        raise ValueError(f"discount_pct must be < 100, got {discount_pct}")
    multiplier = 100 / (100 - discount_pct)
    return price / multiplier


def apply_promo_code(cart_total: float, promo_codes: dict, code: str) -> float:
    """Apply a promotional code to a cart total."""
    # BUG: KeyError when promo_codes dict doesn't have the code
    # (should use .get() with a default)
    discount = promo_codes[code]
    return cart_total * (1 - discount / 100)


def process_refund(order: dict) -> dict:
    """Process a refund for an order."""
    # BUG: Assumes 'refund_amount' key always exists; TypeError on missing key
    refund_amount = order["refund_amount"]
    original = order["total"]
    if refund_amount > original:
        raise ValueError(f"Refund {refund_amount} exceeds order total {original}")
    return {"status": "refunded", "amount": refund_amount}


def split_payment(total: float, num_installments: int) -> list[float]:
    """Split a payment into equal installments."""
    # BUG: ZeroDivisionError when num_installments=0 (allowed by frontend)
    per_installment = total / num_installments
    return [round(per_installment, 2)] * num_installments
