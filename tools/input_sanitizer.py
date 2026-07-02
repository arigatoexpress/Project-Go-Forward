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


def sanitize_query_params(params: dict[str, Any], max_len: int = 500) -> dict[str, Any]:
    """Sanitize query string parameters.

    Query params are typically flat (string -> string or string -> list of strings),
    but we sanitize recursively for safety.
    """
    if not isinstance(params, dict):
        return params
    return {k: sanitize_value(v, max_len) for k, v in params.items()}


_FILENAME_DISALLOWED_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]')
_PATH_TRAVERSAL_RE = re.compile(r'\.{2,}|[/\\]')


def sanitize_filename(name: str | None, max_len: int = 128) -> str | None:
    """Return a safe filename: strip path traversal, control chars, HTML, and length-limit.

    Returns None when input is None or empty after stripping.
    """
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name:
        return None
    # Strip HTML tags first
    name = HTML_TAG_PATTERN.sub("", name)
    # Strip control chars
    name = CONTROL_CHAR_PATTERN.sub("", name)
    # Strip path-traversal patterns and filesystem special chars
    name = _PATH_TRAVERSAL_RE.sub("_", name)
    name = _FILENAME_DISALLOWED_RE.sub("", name)
    # Collapse multiple spaces/underscores
    name = re.sub(r'[\s_]+', '_', name)
    name = name.strip('._')
    if not name:
        return None
    return name[:max_len]
