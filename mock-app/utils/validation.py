"""Input validation utilities."""

import re


ALLOWED_SORT_FIELDS = ["price", "name", "rating", "created_at"]


def validate_email(email: str) -> bool:
    """Basic email validation."""
    return bool(re.match(r"^[^@]+@[^@]+\.[^@]+$", email))


def parse_price_range(range_str: str) -> tuple[float, float]:
    """Parse a price range like '10.00-99.99' into (min, max)."""
    parts = range_str.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid price_range format: {range_str!r}. Expected 'min-max'.")
    return float(parts[0]), float(parts[1])


def validate_sort_field(field: str) -> str:
    """Validate and return a safe sort field name."""
    # BUG: No validation — SQL injection risk if field used directly in query
    # Should check: if field not in ALLOWED_SORT_FIELDS: raise ValueError
    return field


def extract_tags(tag_string: str) -> list[str]:
    """Split comma-separated tags and strip whitespace."""
    # BUG: Crashes with AttributeError if tag_string is None
    return [t.strip() for t in tag_string.split(",") if t.strip()]


def clamp_page_size(size: int, max_size: int = 100) -> int:
    """Ensure page size is within acceptable bounds."""
    # BUG: Doesn't handle size=0, returns 0 which causes downstream issues
    return min(size, max_size)
