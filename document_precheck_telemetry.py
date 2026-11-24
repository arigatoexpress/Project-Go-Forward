"""Server-side record of Document Center pre-check blocks.

The staff Document Center validates a packet in the browser before it calls
``/api/documents/generate-batch``. When that pre-check blocks generation the
server never sees a request, so a broken pre-check (the 2026-11-23 Homestead
"manufacturer model home id is required" outage) left no log line at all.

The browser now posts a small beacon when it blocks. This module turns the
beacon into one WARNING log line per blocked template with a stable
``signature`` so Cloud Logging metrics and the tho-logwatch scan can group it.

Privacy: only template names, the packet name, the pre-check stage and field
*keys* are kept. Every value is checked against config/field_map.json
allowlists; anything else is dropped and only counted. Form values, customer
names, and free text never reach the log.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from config.field_map_loader import get_field_map

EVENT = "documents.precheck_blocked"
MESSAGE = "Document precheck blocked"
SERVICE = "staff-docs"

STAGES = frozenset({"step1", "step2", "completeness", "required", "quality", "test"})
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_TEMPLATES = 60
MAX_FIELDS = 40

_allowlists: tuple[frozenset[str], frozenset[str], frozenset[str]] | None = None


def _load_allowlists() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    global _allowlists
    if _allowlists is None:
        fm = get_field_map()
        templates = frozenset((fm.get("templates") or {}).keys())
        packets = frozenset((fm.get("packets") or {}).keys())
        fields: set[str] = set((fm.get("data_field_definitions") or {}).keys())
        for cfg in (fm.get("templates") or {}).values():
            fields.update(cfg.get("required_fields") or [])
            mapping = cfg.get("field_map") or {}
            if isinstance(mapping, dict):
                fields.update(v for v in mapping.values() if isinstance(v, str))
        _allowlists = (templates, packets, frozenset(k for k in fields if _KEY_RE.match(k)))
    return _allowlists


def _strings(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [v for v in value[:limit] if isinstance(v, str)]


def sanitize_report(data: Any) -> dict[str, Any] | None:
    """Reduce an untrusted beacon body to allowlisted keys. None if unusable."""
    if not isinstance(data, dict):
        return None
    templates_ok, packets_ok, fields_ok = _load_allowlists()

    raw_templates = _strings(data.get("templates"), MAX_TEMPLATES)
    templates = sorted({t for t in raw_templates if t in templates_ok})
    unknown_templates = len([t for t in raw_templates if t not in templates_ok])

    raw_fields = _strings(data.get("missing_fields"), MAX_FIELDS * 2)
    fields = sorted({f for f in raw_fields if f in fields_ok})[:MAX_FIELDS]
    unknown_fields = len([f for f in raw_fields if f not in fields_ok])

    packet = data.get("packet")
    packet = packet if isinstance(packet, str) and packet in packets_ok else None
    stage = data.get("stage")
    stage = stage if isinstance(stage, str) and stage in STAGES else "unknown"
    is_test = data.get("test") is True or stage == "test"

    if not templates and not fields:
        return None
    return {
        "templates": templates,
        "packet": packet,
        "stage": stage,
        "missing_fields": fields,
        "unknown_template_count": unknown_templates,
        "unknown_field_count": unknown_fields,
        "test": is_test,
    }


def signature_for(template: str, stage: str, fields: Iterable[str]) -> str:
    """Stable grouping key: same template, stage and missing keys -> same signature."""
    return f"{EVENT} {template} {stage} [{','.join(sorted(fields))}]"


def build_log_lines(report: dict[str, Any], *, authenticated: bool) -> list[dict[str, Any]]:
    """One WARNING line per blocked template (or one line when none was selected)."""
    now = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
    templates = report["templates"] or ["(none)"]
    lines = []
    for template in templates:
        lines.append(
            {
                "severity": "WARNING",
                "message": MESSAGE,
                "timestamp": now,
                "event": EVENT,
                "lane": "pgf",
                "service": SERVICE,
                "signature": signature_for(template, report["stage"], report["missing_fields"]),
                "template": template,
                "packet": report["packet"] or "(custom)",
                "stage": report["stage"],
                "missing_fields": report["missing_fields"],
                "missing_field_count": len(report["missing_fields"]),
                "selected_template_count": len(report["templates"]),
                "unknown_template_count": report["unknown_template_count"],
                "unknown_field_count": report["unknown_field_count"],
                "authenticated": bool(authenticated),
                "test": bool(report["test"]),
            }
        )
    return lines


def emit_precheck_blocked(
    data: Any,
    *,
    authenticated: bool,
    write: Callable[[str], Any] | None = None,
) -> int:
    """Log a sanitized beacon. Never raises; returns the number of lines written."""
    try:
        report = sanitize_report(data)
        if report is None:
            return 0
        out = write or sys.stdout.write
        lines = build_log_lines(report, authenticated=authenticated)
        for line in lines:
            out(json.dumps(line, separators=(",", ":"), ensure_ascii=True) + "\n")
        if write is None:
            sys.stdout.flush()
        return len(lines)
    except Exception:
        return 0
