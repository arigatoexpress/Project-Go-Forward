#!/usr/bin/env python3
"""50-parallel /healthz/ probes for post-deploy verification.

Reads PROBE_URL from the environment (the Cloud Run service root URL).
Fails (exit 1) if any probe detects:
  - missing Cache-Control: no-store header
  - schema-text placeholder values ("string", "int", "bool", "float") in the body
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import sys
import urllib.error
import urllib.request

SCHEMA_RE = re.compile(r'"(string|int|bool|float)"')
PROBE_COUNT = 50


def probe(i: int) -> str | None:
    base = os.environ["PROBE_URL"].rstrip("/")
    url = base + "/healthz/"
    req = urllib.request.Request(url, headers={"User-Agent": "tho-deploy-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310
            cc = r.headers.get("Cache-Control", "")
            body = r.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return f"PROBE[{i:02d}] FAIL: HTTP {exc.code}"
    except Exception as exc:
        return f"PROBE[{i:02d}] FAIL: {exc}"
    if "no-store" not in cc.lower():
        return f"PROBE[{i:02d}] FAIL: Cache-Control={cc!r} missing no-store"
    m = SCHEMA_RE.search(body)
    if m:
        return f"PROBE[{i:02d}] FAIL: schema-text {m.group(0)!r} in response body"
    return None


def main() -> int:
    url = os.environ.get("PROBE_URL", "")
    if not url:
        print("ERROR: PROBE_URL environment variable not set.", file=sys.stderr)
        return 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=PROBE_COUNT) as pool:
        results = list(pool.map(probe, range(1, PROBE_COUNT + 1)))

    failures = [r for r in results if r is not None]
    for msg in failures:
        print(msg)
    if failures:
        print(f"ERROR: {len(failures)} of {PROBE_COUNT} probes failed.", file=sys.stderr)
        return 1

    print(f"All {PROBE_COUNT} parallel healthz probes passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
