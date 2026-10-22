#!/usr/bin/env python3
"""50-parallel /healthz/ probes for post-deploy verification.

Reads PROBE_URL from the environment (the Cloud Run service root URL).
Fails (exit 1) if any probe detects:
  - missing Cache-Control: no-store header
  - schema-text placeholder values ("string", "int", "bool", "float") in the body

A transport error or HTTP 5xx is retried a bounded number of times. Google's
frontend occasionally substitutes its HTML 502 for one sample while the app
is healthy. A sample that is still failing after those attempts fails the run.
A 200 that is missing no-store, or that contains a schema placeholder, is not
retried.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import sys
import time
import urllib.error
import urllib.request

SCHEMA_RE = re.compile(r'"(string|int|bool|float)"')
PROBE_COUNT = 50
TRANSIENT_ATTEMPTS = 3
TRANSIENT_BACKOFF_SECONDS = (0.5, 1.5)
_BODY_PREFIX_BYTES = 120


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _backoff_after(attempt: int) -> float:
    index = min(attempt - 1, len(TRANSIENT_BACKOFF_SECONDS) - 1)
    return TRANSIENT_BACKOFF_SECONDS[index]


def _body_prefix(body: bytes) -> str:
    return repr(body[:_BODY_PREFIX_BYTES])


def _once(url: str) -> tuple[int, str, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "tho-deploy-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310
            cache_control = response.headers.get("Cache-Control", "") or ""
            content_type = response.headers.get("content-type", "") or ""
            body = response.read(65536)
            return response.status, cache_control, content_type, body
    except urllib.error.HTTPError as exc:
        content_type = ""
        if exc.headers is not None:
            content_type = exc.headers.get("content-type", "") or ""
        return exc.code, "", content_type, exc.read(65536)


def probe(i: int) -> str | None:
    base = os.environ["PROBE_URL"].rstrip("/")
    url = base + "/healthz/"
    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            status, cache_control, content_type, body = _once(url)
        except (TimeoutError, urllib.error.URLError) as exc:
            detail = f"PROBE[{i:02d}] FAIL: {exc}"
            if attempt >= TRANSIENT_ATTEMPTS:
                return detail
            print(
                f"healthz transient attempt {attempt}/{TRANSIENT_ATTEMPTS}: {detail}",
                file=sys.stderr,
            )
            _pause(_backoff_after(attempt))
            continue
        if status >= 500:
            detail = (
                f"PROBE[{i:02d}] FAIL: HTTP {status} content_type={content_type!r} "
                f"body_prefix={_body_prefix(body)}"
            )
            if attempt >= TRANSIENT_ATTEMPTS:
                print(f"healthz persistent failure: {detail}", file=sys.stderr)
                return detail
            print(
                f"healthz transient attempt {attempt}/{TRANSIENT_ATTEMPTS}: {detail}",
                file=sys.stderr,
            )
            _pause(_backoff_after(attempt))
            continue
        if attempt > 1:
            print(
                f"healthz recovered probe {i:02d} status={status} after {attempt} attempts",
                file=sys.stderr,
            )
        text = body.decode("utf-8", errors="replace")
        if "no-store" not in cache_control.lower():
            return f"PROBE[{i:02d}] FAIL: Cache-Control={cache_control!r} missing no-store"
        match = SCHEMA_RE.search(text)
        if match:
            return f"PROBE[{i:02d}] FAIL: schema-text {match.group(0)!r} in response body"
        return None
    return f"PROBE[{i:02d}] FAIL: no response"


def main() -> int:
    url = os.environ.get("PROBE_URL", "")
    if not url:
        print("ERROR: PROBE_URL environment variable not set.", file=sys.stderr)
        return 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=PROBE_COUNT) as pool:
        results = list(pool.map(probe, range(1, PROBE_COUNT + 1)))

    failures = [result for result in results if result is not None]
    for msg in failures:
        print(msg)
    if failures:
        print(f"ERROR: {len(failures)} of {PROBE_COUNT} probes failed.", file=sys.stderr)
        return 1

    print(f"All {PROBE_COUNT} parallel healthz probes passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
