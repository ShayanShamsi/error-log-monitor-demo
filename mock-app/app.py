"""
Mock e-commerce API — used to generate realistic error logs.
Run with: uv run uvicorn app:app
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import logging
import sys

from services.payment import calculate_discount, apply_promo_code, split_payment, process_refund
from services.database import get_user, update_user_balance, slow_query, get_user_orders
from services.cache import get, set, get_stats, invalidate_user
from utils.validation import parse_price_range, extract_tags, clamp_page_size, validate_sort_field

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ecommerce-api")

app = FastAPI(title="Mock E-Commerce API")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/users/{user_id}")
def get_user_endpoint(user_id: str):
    cached = get(f"user:{user_id}:profile")
    if cached:
        return cached
    user = get_user(user_id)
    # BUG: user could be None — will crash on the next line
    set(f"user:{user_id}:profile", user)
    return user


@app.post("/cart/discount")
def apply_discount(price: float, discount_pct: float):
    try:
        result = calculate_discount(price, discount_pct)
        return {"discounted_price": result}
    except ZeroDivisionError as e:
        logger.error("ZeroDivisionError in calculate_discount: price=%s discount_pct=%s", price, discount_pct)
        raise HTTPException(status_code=400, detail="Invalid discount percentage")


@app.post("/cart/promo")
def apply_promo(cart_total: float, code: str):
    promo_codes = {"SAVE10": 10, "WELCOME20": 20, "FLASH50": 50}
    try:
        result = apply_promo_code(cart_total, promo_codes, code)
        return {"final_total": result}
    except KeyError:
        logger.warning("Unknown promo code attempted: %s", code)
        raise HTTPException(status_code=404, detail=f"Promo code '{code}' not found")


@app.get("/products")
def list_products(price_range: str = None, tags: str = None, sort: str = "price", page_size: int = 20):
    result = {}
    if price_range:
        try:
            min_p, max_p = parse_price_range(price_range)
            result["price_filter"] = {"min": min_p, "max": max_p}
        except (IndexError, ValueError) as e:
            logger.error("Failed to parse price_range=%r: %s", price_range, e)
            raise HTTPException(status_code=400, detail="Invalid price_range format")
    if tags:
        result["tags"] = extract_tags(tags)
    result["sort"] = validate_sort_field(sort)
    result["page_size"] = clamp_page_size(page_size)
    return result


@app.post("/payments/split")
def split_payment_endpoint(total: float, installments: int):
    try:
        parts = split_payment(total, installments)
        return {"installments": parts}
    except ZeroDivisionError:
        logger.error("split_payment called with installments=0, total=%s", total)
        raise HTTPException(status_code=400, detail="installments must be > 0")


@app.post("/users/{user_id}/balance")
def update_balance(user_id: str, delta: float):
    try:
        update_user_balance(user_id, delta)
        return {"status": "ok"}
    except TypeError as e:
        logger.error("TypeError updating balance for user_id=%s: %s", user_id, e)
        raise HTTPException(status_code=404, detail="User not found")


@app.get("/cache/stats")
def cache_stats():
    return get_stats()


@app.delete("/cache/user/{user_id}")
def invalidate_cache(user_id: str):
    try:
        count = invalidate_user(user_id)
        return {"invalidated": count}
    except RuntimeError as e:
        logger.error("RuntimeError during cache invalidation for user_id=%s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Cache invalidation failed")
