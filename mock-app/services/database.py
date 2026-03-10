"""Database access layer."""

import time
import random


# Simulated in-memory "database"
_USERS = {
    "u001": {"name": "Alice", "email": "alice@example.com", "balance": 150.00},
    "u002": {"name": "Bob", "email": "bob@example.com", "balance": 0.00},
    "u003": {"name": "Carol", "email": "carol@example.com", "balance": 320.50},
}

_ORDERS = {
    "o001": {"user_id": "u001", "total": 89.99, "status": "shipped"},
    "o002": {"user_id": "u002", "total": 24.50, "status": "pending"},
}


def get_user(user_id: str) -> dict:
    """Fetch user by ID."""
    # BUG: Returns None silently, callers crash with AttributeError
    # Should raise a proper NotFoundError
    return _USERS.get(user_id)


def get_user_orders(user_id: str) -> list[dict]:
    """Get all orders for a user."""
    return [o for o in _ORDERS.values() if o["user_id"] == user_id]


def update_user_balance(user_id: str, delta: float) -> None:
    """Add/subtract from user balance."""
    user = _USERS.get(user_id)
    if user is None:
        raise KeyError(f"User not found: {user_id}")
    user["balance"] += delta


def slow_query(query_id: str) -> dict:
    """Simulates an occasionally slow DB query (connection pool exhaustion)."""
    # 15% chance of a long-running query
    if random.random() < 0.15:
        time.sleep(6)  # Exceeds typical 5s timeout
    return {"query_id": query_id, "rows": random.randint(1, 500)}
