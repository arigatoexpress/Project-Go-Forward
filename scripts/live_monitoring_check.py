#!/usr/bin/env python3
"""Live monitoring check for the THO Mira bridge, cutover status, and ops signals.

Read-only by design. Exercises public + partner-protected endpoints, reports
JSON, and exits non-zero when any check fails.

Usage:
    python scripts/live_monitoring_check.py
    python scripts/live_monitoring_check.py --base-url https://www.texashomeoutlet.com --api-key "$THO_API_KEY"
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://tho.sapphirealpha.xyz"
DEFAULT_TIMEOUT = 20.0
DEFAULT_LEAD_BACKLOG_THRESHOLD = 50

# Endpoints that do not require a partner API key.
PUBLIC_ENDPOINTS = (
    "/healthz/",
    "/api/v1/mira/health",
)

# Partner-protected Mira endpoints and the JSON keys they must return.
MIRA_ENDPOINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/api/v1/mira/system", ("status", "version")),
    ("/api/v1/mira/metrics", ("status", "metrics")),
    ("/api/v1/mira/leads/summary", ("status", "total", "by_status")),
    ("/api/v1/mira/leads/recent", ("status", "count", "leads")),
    ("/api/v1/mira/leads/triage", ("status", "count", "leads")),
    ("/api/v1/mira/appointments/summary", ("status", "total", "by_status")),
    ("/api/v1/mira/installations/summary", ("status", "total", "by_status")),
    ("/api/v1/mira/installations/recent", ("status", "count", "items")),
    ("/api/v1/mira/feedback/summary", ("status", "total")),
    ("/api/v1/mira/feedback/recent", ("status", "count", "items")),
    ("/api/v1/mira/firestore/collections", ("status", "collections")),
    ("/api/v1/mira/chat/summary", ("status", "hours")),
)

# Other partner-protected status endpoints.
STATUS_ENDPOINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/api/v1/github/mira/status", ("configured", "webhook_secret_set", "mira_group_configured")),
    ("/api/v1/cutover/dns-status", ("domain", "apex", "www", "overall_ready")),
    ("/api/v1/cutover/mx-status", ("domain", "inbound", "resend", "overall_ready")),
)


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    status: int | None
    evidence: str
    elapsed_ms: int


def _get(
    base_url: str,
    path: str,
    *,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int | None, bytes, str, int]:
    """HTTP GET a path and return (status, body, content_type, elapsed_ms)."""
    url = base_url.rstrip("/") + path
    headers = {"User-Agent": "tho-live-monitoring-check/1.0", "Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - operator-provided HTTPS URL.
            body = response.read(2 * 1024 * 1024)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return (
                response.status,
                body,
                response.headers.get("content-type", ""),
                elapsed_ms,
            )
    except HTTPError as exc:
        body = exc.read(256 * 1024)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return exc.code, body, exc.headers.get("content-type", ""), elapsed_ms
    except URLError:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return None, b"", "", elapsed_ms
    except Exception as exc:  # pragma: no cover - defensive
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return None, str(exc).encode("utf-8"), "", elapsed_ms


def _json_body(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _check_endpoint(
    base_url: str,
    path: str,
    api_key: str | None,
    timeout: float,
    required_keys: tuple[str, ...],
    name: str | None = None,
) -> Probe:
    status, body, content_type, elapsed_ms = _get(base_url, path, api_key=api_key, timeout=timeout)
    display_name = name or path

    if status is None:
        return Probe(display_name, False, None, "network error", elapsed_ms)
    if status != 200:
        return Probe(display_name, False, status, f"HTTP {status} (content-type={content_type})", elapsed_ms)

    payload = _json_body(body)
    if payload is None:
        return Probe(display_name, False, status, "non-JSON response", elapsed_ms)

    missing = [key for key in required_keys if key not in payload]
    if missing:
        return Probe(display_name, False, status, f"missing keys: {', '.join(missing)}", elapsed_ms)

    # The Mira endpoints return HTTP 200 with {"status": "error"} on a Firestore
    # failure (graceful degradation, never 500). Key-presence alone would read
    # that as healthy — blind to an outage exactly when it matters. Assert the
    # value too.
    body_status = payload.get("status")
    if "status" in required_keys and body_status not in ("ok", "healthy"):
        return Probe(display_name, False, status, f"body status={body_status!r}", elapsed_ms)

    return Probe(display_name, True, status, f"ok keys={','.join(required_keys)}", elapsed_ms)


def _check_lead_backlog(
    base_url: str,
    api_key: str | None,
    timeout: float,
    threshold: int,
) -> tuple[Probe, list[str]]:
    """Return the leads-summary probe plus any backlog warnings."""
    probe = _check_endpoint(
        base_url,
        "/api/v1/mira/leads/summary",
        api_key,
        timeout,
        ("status", "total", "by_status"),
        name="lead backlog",
    )
    warnings: list[str] = []
    if not probe.ok:
        return probe, warnings

    # Re-fetch body to parse counts; cheap compared to a real Firestore query.
    status, body, _, _ = _get(base_url, "/api/v1/mira/leads/summary", api_key=api_key, timeout=timeout)
    payload = _json_body(body) if status == 200 else None
    if payload:
        by_status = payload.get("by_status") or {}
        new_count = by_status.get("new", 0)
        if new_count > threshold:
            warnings.append(
                f"{new_count} leads in 'new' status exceed threshold ({threshold})"
            )
    return probe, warnings


def run_checks(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    lead_threshold: int = DEFAULT_LEAD_BACKLOG_THRESHOLD,
) -> tuple[list[Probe], list[str]]:
    """Run the full monitoring suite and return (probes, warnings)."""
    probes: list[Probe] = []
    warnings: list[str] = []

    for path in PUBLIC_ENDPOINTS:
        probes.append(_check_endpoint(base_url, path, None, timeout, ("status",), name=path))

    if not api_key:
        probes.append(
            Probe(
                "partner endpoints",
                False,
                None,
                "skipped: no API key (set THO_API_KEY or pass --api-key)",
                0,
            )
        )
    else:
        for path, required_keys in MIRA_ENDPOINTS:
            probes.append(_check_endpoint(base_url, path, api_key, timeout, required_keys))
        for path, required_keys in STATUS_ENDPOINTS:
            probes.append(_check_endpoint(base_url, path, api_key, timeout, required_keys))

    if api_key:
        lead_probe, warnings = _check_lead_backlog(base_url, api_key, timeout, lead_threshold)
        # Replace the generic leads/summary probe from MIRA_ENDPOINTS with the backlog-named one.
        probes = [p for p in probes if p.name != "/api/v1/mira/leads/summary"]
        probes.append(lead_probe)

        # Surface DNS/MX readiness warnings.
        for path in ("/api/v1/cutover/dns-status", "/api/v1/cutover/mx-status"):
            probe = next((p for p in probes if p.name == path), None)
            if not probe or not probe.ok:
                continue
            status, body, _, _ = _get(base_url, path, api_key=api_key, timeout=timeout)
            payload = _json_body(body) if status == 200 else None
            if payload and not payload.get("overall_ready"):
                label = "DNS" if "dns" in path else "MX"
                warnings.append(f"{label} cutover is not yet overall_ready")

    return probes, warnings


def _format_report(probes: list[Probe], warnings: list[str]) -> dict[str, Any]:
    failed = [p for p in probes if not p.ok]
    return {
        "summary": {
            "total": len(probes),
            "passed": len([p for p in probes if p.ok]),
            "failed": len(failed),
            "warnings": warnings,
        },
        "probes": [asdict(p) for p in probes],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live monitoring check for THO Mira bridge, cutover status, and ops signals."
    )
    parser.add_argument("--base-url", default=os.environ.get("THO_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("THO_API_KEY"))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--lead-threshold", type=int, default=DEFAULT_LEAD_BACKLOG_THRESHOLD)
    args = parser.parse_args(argv)

    probes, warnings = run_checks(
        args.base_url,
        args.api_key,
        timeout=args.timeout,
        lead_threshold=args.lead_threshold,
    )
    report = _format_report(probes, warnings)
    print(json.dumps(report, indent=2))

    failed = [p for p in probes if not p.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
