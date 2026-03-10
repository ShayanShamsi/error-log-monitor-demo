"""
Generate realistic error logs for the mock e-commerce app.
Simulates ~8 hours of production traffic with known bug patterns.

Usage:  uv run python generate_logs.py [--hours 8] [--out ../logs/app.log]
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Error templates — each maps to a real bug in mock-app/
# ---------------------------------------------------------------------------

ERRORS = [
    {
        "id": "ERR_ZERO_DIV_DISCOUNT",
        "level": "ERROR",
        "logger": "ecommerce-api",
        "message": "ZeroDivisionError in calculate_discount: price={price} discount_pct=100",
        "traceback": (
            'Traceback (most recent call last):\n'
            '  File "/app/services/payment.py", line 10, in calculate_discount\n'
            '    multiplier = 100 / (100 - discount_pct)\n'
            'ZeroDivisionError: division by zero'
        ),
        "file": "services/payment.py",
        "line": 10,
        "weight": 15,  # relative frequency
    },
    {
        "id": "ERR_KEY_PROMO",
        "level": "WARNING",
        "logger": "ecommerce-api",
        "message": "Unknown promo code attempted: {code}",
        "traceback": None,
        "file": None,
        "line": None,
        "weight": 30,
    },
    {
        "id": "ERR_INDEX_PRICE_RANGE",
        "level": "ERROR",
        "logger": "ecommerce-api",
        "message": "Failed to parse price_range={price_range!r}: list index out of range",
        "traceback": (
            'Traceback (most recent call last):\n'
            '  File "/app/utils/validation.py", line 17, in parse_price_range\n'
            '    return float(parts[0]), float(parts[1])\n'
            'IndexError: list index out of range'
        ),
        "file": "utils/validation.py",
        "line": 17,
        "weight": 20,
    },
    {
        "id": "ERR_NONE_USER_BALANCE",
        "level": "ERROR",
        "logger": "ecommerce-api",
        "message": "TypeError updating balance for user_id={user_id}: 'NoneType' object does not support item assignment",
        "traceback": (
            'Traceback (most recent call last):\n'
            '  File "/app/services/database.py", line 35, in update_user_balance\n'
            '    user["balance"] += delta\n'
            "TypeError: 'NoneType' object does not support item assignment"
        ),
        "file": "services/database.py",
        "line": 35,
        "weight": 10,
    },
    {
        "id": "ERR_RUNTIME_CACHE_INVALIDATE",
        "level": "ERROR",
        "logger": "ecommerce-api",
        "message": "RuntimeError during cache invalidation for user_id={user_id}: dictionary changed size during iteration",
        "traceback": (
            'Traceback (most recent call last):\n'
            '  File "/app/services/cache.py", line 42, in invalidate_user\n'
            '    for key in _cache:\n'
            'RuntimeError: dictionary changed size during iteration'
        ),
        "file": "services/cache.py",
        "line": 42,
        "weight": 8,
    },
    {
        "id": "ERR_SPLIT_ZERO_INSTALLMENTS",
        "level": "ERROR",
        "logger": "ecommerce-api",
        "message": "split_payment called with installments=0, total={total}",
        "traceback": (
            'Traceback (most recent call last):\n'
            '  File "/app/services/payment.py", line 30, in split_payment\n'
            '    per_installment = total / num_installments\n'
            'ZeroDivisionError: float division by zero'
        ),
        "file": "services/payment.py",
        "line": 30,
        "weight": 5,
    },
    # Noise / benign warnings
    {
        "id": "WARN_SLOW_QUERY",
        "level": "WARNING",
        "logger": "ecommerce-api",
        "message": "Slow query detected: query_id={query_id} duration={duration:.2f}s (threshold=5.0s)",
        "traceback": None,
        "file": None,
        "line": None,
        "weight": 25,
    },
    {
        "id": "INFO_CACHE_HIT",
        "level": "INFO",
        "logger": "ecommerce-api",
        "message": "Cache hit for key=user:{user_id}:profile",
        "traceback": None,
        "file": None,
        "line": None,
        "weight": 60,
    },
    {
        "id": "INFO_REQUEST",
        "level": "INFO",
        "logger": "uvicorn.access",
        "message": '{ip} - "GET /products HTTP/1.1" {status} -',
        "traceback": None,
        "file": None,
        "line": None,
        "weight": 120,
    },
]

PROMO_CODES_BAD = ["DISCOUNT50", "FREESHIP", "VIP100", "SUMMER99", "XMAS2024"]
USER_IDS = [f"u{i:03d}" for i in range(1, 20)]
PRICES = [9.99, 24.50, 49.99, 89.99, 149.99, 299.00]


def render(template: str, **kwargs) -> str:
    try:
        return template.format(**kwargs)
    except KeyError:
        return template


def make_entry(ts: datetime, error: dict) -> dict:
    eid = error["id"]
    params: dict = {}

    if "price" in error["message"]:
        params["price"] = random.choice(PRICES)
    if "discount_pct" in error["message"]:
        params["discount_pct"] = 100
    if "code" in error["message"] or "promo" in eid.lower():
        params["code"] = random.choice(PROMO_CODES_BAD)
    if "price_range" in error["message"]:
        params["price_range"] = random.choice(["50", "badvalue", "abc"])
    if "user_id" in error["message"]:
        params["user_id"] = random.choice(USER_IDS)
    if "total" in error["message"]:
        params["total"] = random.choice(PRICES)
    if "query_id" in error["message"]:
        params["query_id"] = f"q{random.randint(1000,9999)}"
        params["duration"] = random.uniform(5.1, 12.0)
    if "ip" in error["message"]:
        params["ip"] = f"10.0.{random.randint(0,10)}.{random.randint(1,254)}"
        params["status"] = random.choice([200, 200, 200, 404, 500])

    entry = {
        "timestamp": ts.isoformat(),
        "level": error["level"],
        "logger": error["logger"],
        "message": render(error["message"], **params),
        "error_id": eid,
    }
    if error.get("traceback"):
        entry["traceback"] = error["traceback"]
    if error.get("file"):
        entry["file"] = error["file"]
        entry["line"] = error["line"]
    return entry


def generate(hours: float, out_path: Path) -> int:
    weights = [e["weight"] for e in ERRORS]
    now = datetime.utcnow()
    start = now - timedelta(hours=hours)

    # ~150 events per hour
    n_events = int(hours * 150)
    timestamps = sorted(
        start + timedelta(seconds=random.uniform(0, hours * 3600))
        for _ in range(n_events)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w") as f:
        for ts in timestamps:
            error = random.choices(ERRORS, weights=weights, k=1)[0]
            entry = make_entry(ts, error)
            f.write(json.dumps(entry) + "\n")
            count += 1

    return count


def main():
    parser = argparse.ArgumentParser(description="Generate mock error logs")
    parser.add_argument("--hours", type=float, default=8.0, help="Hours of traffic to simulate")
    parser.add_argument("--out", default="../logs/app.log", help="Output log file path")
    args = parser.parse_args()

    out = Path(args.out)
    n = generate(args.hours, out)
    print(f"Generated {n} log entries -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
