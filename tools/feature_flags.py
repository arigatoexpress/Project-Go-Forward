"""Centralized feature-flag system for safe rollouts and kill-switches.

Project-Go-Forward uses env-var gates for several features (inbound email,
Notion Command Center, image-processing bypass, etc.). This module centralises
those checks into a single, testable, auditable surface so operators can toggle
features without redeploying.

Design
------
- **Read at call time** (never import time) so tests and runtime config flips
  take effect immediately.
- **No exceptions outward.** On any error the flag degrades to its safe default.
- **Three sources, strict priority:**
  1. Environment variables  – ``FF_<NAME>`` (bool), ``FF_<NAME>_VALUE`` (str),
     ``FF_<NAME>_PERCENT`` (int 0-100)
  2. ``config.yaml`` ``feature_flags:`` block (if present, never required)
  3. Hard-coded safe default passed by the caller
- **Percentage rollouts.** When ``FF_<NAME>_PERCENT`` is set, a deterministic
  hash of ``user_id + flag_name`` decides inclusion, so the same user always
  gets the same experience.
- **PII-safe.** No user state is stored; the hash is computed on the fly.

Usage
-----
    import tools.feature_flags as ff

    if ff.is_enabled("INBOUND_EMAIL"):
        ...

    if ff.is_enabled_for_user("NEW_UI", user_id=session["user_id"]):
        ...

    pct = ff.get_percentage("GRADUAL_ROLLOUT", default=0)

    flags = ff.all_flags()   # for admin dashboards
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from structured_logging import logger as struct_logger


def _env(name: str) -> str | None:
    """Read ``name`` from ``os.environ``; return ``None`` if absent or empty."""
    val = os.environ.get(name)
    return val.strip() if val else None


def _read_env_bool(name: str) -> str | None:
    """Look for ``FF_<NAME>`` or ``FEATURE_FLAG_<NAME>``."""
    for prefix in ("FF_", "FEATURE_FLAG_"):
        val = _env(f"{prefix}{name}")
        if val is not None:
            return val
    return None


def _read_env_value(name: str) -> str | None:
    """Look for ``FF_<NAME>_VALUE`` or ``FEATURE_FLAG_<NAME>_VALUE``."""
    for prefix in ("FF_", "FEATURE_FLAG_"):
        val = _env(f"{prefix}{name}_VALUE")
        if val is not None:
            return val
    return None


def _read_env_percent(name: str) -> str | None:
    """Look for ``FF_<NAME>_PERCENT`` or ``FEATURE_FLAG_<NAME>_PERCENT``."""
    for prefix in ("FF_", "FEATURE_FLAG_"):
        val = _env(f"{prefix}{name}_PERCENT")
        if val is not None:
            return val
    return None


def _read_config(name: str) -> Any:
    """Read from ``config.yaml`` ``feature_flags:<name>`` if present."""
    try:
        from config_loader import get_config

        cfg = (get_config() or {}).get("feature_flags") or {}
        return cfg.get(name)
    except Exception:  # noqa: BLE001 — config block is optional; degrade gracefully
        return None


def _get_raw(name: str) -> str | None:
    """Resolve raw value: env first, then config, then ``None``."""
    # Boolean-style env var
    env_bool = _read_env_bool(name)
    if env_bool is not None:
        return env_bool
    # Value-style env var
    env_val = _read_env_value(name)
    if env_val is not None:
        return env_val
    # Config fallback
    cfg_val = _read_config(name)
    if cfg_val is not None:
        return str(cfg_val)
    return None


def _get_raw_percent(name: str) -> str | None:
    """Resolve percentage value: env first, then config, then ``None``."""
    env_pct = _read_env_percent(name)
    if env_pct is not None:
        return env_pct
    # Config may contain an integer directly
    cfg_val = _read_config(name)
    if cfg_val is not None:
        return str(cfg_val)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_enabled(name: str, default: bool = False) -> bool:
    """Return ``True`` when the named flag is explicitly enabled.

    A flag is **enabled** when its raw value is one of the truthy tokens:
    ``1``, ``true``, ``yes``, ``on``, ``enabled`` (case-insensitive).
    Any other non-empty value is treated as **disabled** (returns ``False``).

    If the flag is not configured anywhere, ``default`` is returned.
    """
    raw = _get_raw(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "enabled"}


def is_disabled(name: str, default: bool = False) -> bool:
    """Return ``True`` when the named flag is explicitly disabled.

    A flag is **disabled** when its raw value is one of the falsy tokens:
    ``0``, ``false``, ``no``, ``off``, ``disabled`` (case-insensitive).
    Any other non-empty value is treated as **enabled** (returns ``True``).

    If the flag is not configured anywhere, ``default`` is returned.
    """
    raw = _get_raw(name)
    if raw is None:
        return default
    return raw.lower() in {"0", "false", "no", "off", "disabled"}


def get_value(name: str, default: str | None = None) -> str | None:
    """Return the flag's raw string value, or ``default`` if absent."""
    raw = _get_raw(name)
    if raw is None:
        return default
    return raw


def get_percentage(name: str, default: int = 0) -> int:
    """Return the flag's rollout percentage, clamped to ``[0, 100]``.

    Percentage is read from ``FF_<NAME>_PERCENT`` or the config value.
    If the value cannot be parsed as an integer, ``default`` is returned.
    """
    raw = _get_raw_percent(name)
    if raw is None:
        return default
    try:
        return max(0, min(100, int(raw)))
    except ValueError:
        return default


def is_enabled_for_user(name: str, user_id: str, default: bool = False) -> bool:
    """Deterministic percentage-based rollout for a specific user.

    1. If the flag is not configured, return ``default``.
    2. If the flag is explicitly disabled (falsy token), return ``False``.
    3. If the flag is explicitly enabled without a percentage, return ``True``.
    4. If a percentage is configured, hash ``name + user_id`` into a bucket
       ``[0, 99]`` and include the user when ``bucket < percentage``.

    This is **stable** — the same ``(name, user_id)`` pair always yields the
    same result until the percentage changes.
    """
    raw = _get_raw(name)
    if raw is None:
        return default

    lowered = raw.lower()
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False

    pct = get_percentage(name, default=-1)
    if pct < 0:  # no percentage configured — treat as boolean enabled
        return True

    if pct >= 100:
        return True
    if pct <= 0:
        return False

    # Deterministic bucket: first 8 hex chars of SHA-256 -> 32-bit int -> 0-99
    h = hashlib.sha256(f"{name}:{user_id}".encode()).hexdigest()
    bucket = int(h[:8], 16) % 100
    return bucket < pct


def all_flags() -> dict[str, Any]:
    """Return a snapshot of all flags known from env vars and config.

    The returned dict is safe for JSON serialization and contains no secrets.
    """
    flags: dict[str, Any] = {}

    # ---- Env vars -----------------------------------------------------------
    for key, val in os.environ.items():
        name = None
        if key.startswith("FF_"):
            name = key[3:]
        elif key.startswith("FEATURE_FLAG_"):
            name = key[13:]
        else:
            continue

        # Skip the *_VALUE / *_PERCENT suffixes — they belong to the base flag
        if name.endswith("_VALUE") or name.endswith("_PERCENT"):
            base = name[:-6] if name.endswith("_VALUE") else name[:-8]
            if base not in flags:
                flags[base] = _flag_snapshot(base)
            continue

        if name not in flags:
            flags[name] = _flag_snapshot(name)

    # ---- Config.yaml --------------------------------------------------------
    try:
        from config_loader import get_config

        cfg = (get_config() or {}).get("feature_flags") or {}
        for name in cfg:
            if name not in flags:
                flags[name] = _flag_snapshot(name)
    except Exception as e:  # noqa: BLE001
        struct_logger.error("feature_flags config read failed", error=str(e))

    return flags


def _flag_snapshot(name: str) -> dict[str, Any]:
    """Build a JSON-safe snapshot for a single flag."""
    return {
        "enabled": is_enabled(name, default=False),
        "disabled": is_disabled(name, default=False),
        "value": get_value(name),
        "percentage": get_percentage(name, default=0),
    }
