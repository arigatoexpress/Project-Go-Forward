#!/usr/bin/env python3
"""Warm a no-traffic Cloud Run candidate before deploy smoke.

The candidate tag URL receives no traffic, so the first request pays container
startup and the smoke's GET / timing jumps around. This script requests GET /
until one response finishes under the latency target, then exits. Redirects
are not followed: a candidate ``*.run.app`` host answers ``/`` with 308 to the
public domain, and that response is the candidate's own ``/``.

Bounds, so a dead candidate fails instead of hanging the deploy:

* attempt 1 timeout: 20s (room for a cold start)
* attempt 2 timeout: 8s
* attempt 3 timeout: 5s
* pause between attempts: 1s

Worst case is 35s (20 + 1 + 8 + 1 + 5). A 4xx other than a redirect fails
immediately. A transport error or HTTP 5xx uses another attempt.
"""

from __future__ import annotations

import argparse
import http.client
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

TIMEOUTS_S = (20.0, 8.0, 5.0)
PAUSE_S = 1.0
TARGET_MS = 1500
# 200 is a rendered page. 301/302/307/308 are the candidate host's own
# answer for ``/`` (the live storefront sends browsers to the public domain).
SUCCESS_STATUSES = frozenset({200, 301, 302, 307, 308})
_BODY_LIMIT = 1024 * 1024


def worst_case_seconds() -> float:
    """Upper bound on time spent in requests and pauses. Excludes process startup."""
    if not TIMEOUTS_S:
        return 0.0
    return sum(TIMEOUTS_S) + PAUSE_S * (len(TIMEOUTS_S) - 1)


def root_url(base_url: str) -> str:
    """Origin plus ``/``. Rejects non-HTTP URLs so the warmer cannot read local files."""
    parts = urlsplit((base_url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base-url must be an http(s) origin")
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop on the candidate's own response instead of following it to production."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _fetch(url: str, timeout: float) -> tuple[int, int]:
    """GET ``url`` once. Returns ``(status, elapsed_ms)``. Transport errors raise RuntimeError."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tho-candidate-warmup/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    started = time.perf_counter()
    try:
        try:
            with opener.open(request, timeout=timeout) as response:  # nosec B310 - operator HTTPS URL
                response.read(_BODY_LIMIT)
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            exc.read(_BODY_LIMIT)
            status = int(exc.code)
    except TimeoutError as exc:
        raise RuntimeError(f"timed out after {timeout:g}s") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc}") from exc
    except (ConnectionResetError, http.client.IncompleteRead) as exc:
        raise RuntimeError(f"network error: {exc}") from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return status, elapsed_ms


def warm(base_url: str) -> dict[str, object]:
    """Request GET / until one response is under ``TARGET_MS``, or the budget is spent."""
    try:
        url = root_url(base_url)
    except ValueError as exc:
        return {"ok": False, "status": None, "latency_ms": None, "attempts": 0, "error": str(exc)}

    attempts = len(TIMEOUTS_S)
    last_error = "no response"
    for index, timeout in enumerate(TIMEOUTS_S, start=1):
        try:
            status, elapsed_ms = _fetch(url, timeout)
        except RuntimeError as exc:
            last_error = str(exc)
            print(
                f"warmup attempt {index}/{attempts}: {last_error} timeout_s={timeout:g}",
                file=sys.stderr,
            )
        else:
            print(
                f"warmup attempt {index}/{attempts}: status={status} "
                f"elapsed_ms={elapsed_ms} target_ms={TARGET_MS}",
                file=sys.stderr,
            )
            if status in SUCCESS_STATUSES and elapsed_ms < TARGET_MS:
                print(
                    f"candidate warm: GET / latency_ms={elapsed_ms} status={status}",
                    file=sys.stderr,
                )
                return {
                    "ok": True,
                    "status": status,
                    "latency_ms": elapsed_ms,
                    "attempts": index,
                    "error": None,
                }
            if status in SUCCESS_STATUSES or status >= 500:
                last_error = f"status={status} elapsed_ms={elapsed_ms}"
            else:
                message = f"candidate GET / returned HTTP {status}; not retrying"
                print(f"ERROR: {message}", file=sys.stderr)
                return {
                    "ok": False,
                    "status": status,
                    "latency_ms": elapsed_ms,
                    "attempts": index,
                    "error": message,
                }
        if index < attempts:
            _pause(PAUSE_S)

    message = (
        f"candidate GET / did not respond under {TARGET_MS}ms within {attempts} attempts"
        f" (last: {last_error})"
    )
    print(f"ERROR: {message}", file=sys.stderr)
    return {
        "ok": False,
        "status": None,
        "latency_ms": None,
        "attempts": attempts,
        "error": message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args(argv)
    result = warm(args.base_url)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
