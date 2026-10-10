"""Replay protection helpers for signed webhooks (standard library only).

``ReplayGuard`` is a small per-process TTL set. It stops an exact replay that
lands on the same instance inside the window; it is not a cross-instance
store. Pair it with a timestamp window (which works on every instance) and
idempotent handlers.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from typing import Any


class ReplayGuard:
    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        while self._seen:
            key, expires = next(iter(self._seen.items()))
            if expires > now and len(self._seen) <= self._max:
                break
            self._seen.popitem(last=False)

    def claim(self, key: str) -> bool:
        """Return True the first time ``key`` is seen inside the TTL, else False."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            if key in self._seen:
                return False
            self._seen[key] = now + self._ttl
            self._expire(now)
            return True

    def release(self, key: str) -> None:
        """Forget ``key`` so a sender retry after a failed attempt is processed."""
        with self._lock:
            self._seen.pop(key, None)


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    return None


def docuseal_event_key(
    event: dict[str, Any],
    body: bytes,
    *,
    now: float | None = None,
    max_age_seconds: float = 72 * 3600,
    future_skew_seconds: float = 300,
) -> tuple[str | None, str | None]:
    """Return ``(dedupe_key, None)`` or ``(None, reject_reason)`` for a DocuSeal event.

    A payload ``timestamp`` outside the window is rejected. A payload without one
    is still accepted (backward compatible) and deduplicated on its exact bytes.
    """
    current = time.time() if now is None else now
    raw_ts = event.get("timestamp") if isinstance(event, dict) else None
    if raw_ts is not None:
        ts = _parse_timestamp(raw_ts)
        if ts is None:
            return None, "invalid_timestamp"
        if ts < current - max_age_seconds:
            return None, "stale_timestamp"
        if ts > current + future_skew_seconds:
            return None, "future_timestamp"
    data = event.get("data") if isinstance(event, dict) else None
    event_type = (
        str(event.get("event_type") or event.get("type") or "") if isinstance(event, dict) else ""
    )
    event_id = data.get("id") if isinstance(data, dict) else None
    if event_type and event_id is not None and raw_ts is not None:
        return f"docuseal:{event_type}:{event_id}:{raw_ts}", None
    return "docuseal:body:" + hashlib.sha256(body).hexdigest(), None
