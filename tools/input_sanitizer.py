"""Input sanitization utilities — strips HTML, control chars, and truncates long strings."""
import re
from typing import Any

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_string(val: str, max_len: int = 500) -> str:
    """Strip HTML tags, control chars, and truncate."""
    if not isinstance(val, str):
        return val
    val = HTML_TAG_PATTERN.sub("", val)
    val = CONTROL_CHAR_PATTERN.sub("", val)
    val = val.strip()
    return val[:max_len]


def sanitize_value(val: Any, max_len: int = 500) -> Any:
    """Recursively sanitize strings in dicts, lists, and tuples."""
    if isinstance(val, str):
        return sanitize_string(val, max_len)
    if isinstance(val, dict):
        return {k: sanitize_value(v, max_len) for k, v in val.items()}
    if isinstance(val, list):
        return [sanitize_value(v, max_len) for v in val]
    if isinstance(val, tuple):
        return tuple(sanitize_value(v, max_len) for v in val)
    return val


def sanitize_body(data: Any, max_len: int = 500) -> Any:
    """Top-level entry point for request-body sanitization."""
    return sanitize_value(data, max_len)
