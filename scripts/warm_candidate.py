#!/usr/bin/env python3
"""Warm a no-traffic Cloud Run candidate before deploy smoke.

The candidate tag URL receives no traffic, so the first real request pays
container startup and later smoke timings jump around. This script requests
paths that the tag host answers itself:

* ``/healthz/`` (liveness JSON)
* ``/api/marketing/inventory-context`` (public catalog the smoke reads)
* ``/staff`` (sign-in page HTML)

On a candidate ``*.run.app`` host, ``GET /`` is a 308 to the public site and
does not render. A 3xx does not count as warm. Redirects are not followed.

Bounds, so a dead candidate fails instead of hanging the deploy:

* attempt 1 timeout: 20s (room for a cold start)
* attempt 2 timeout: 8s
* attempt 3 timeout: 5s
* pause between attempts: 1s

The timeout is the budget for the whole attempt, shared across those paths.
Worst case is 35s (20 + 1 + 8 + 1 + 5). A 3xx or other 4xx fails immediately.
A transport error, HTTP 5xx, or a 200 that is still slower than the target
uses another attempt.
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
# Paths the candidate tag host answers itself. ``GET /`` is omitted: on
# ``*.run.app`` the canonical-host middleware returns 308 to the public site.
WARM_PATHS = (
    "/healthz/",
    "/api/marketing/inventory-context",
    "/staff",
)
# Only a direct 200 is warm. Redirects used to count, which let a fast 308
# pass the step without loading the catalog or the staff page.
SUCCESS_STATUS = 200
_BODY_LIMIT = 8 * 1024 * 1024


def worst_case_seconds() -> float:
    """Upper bound on time spent in requests and pauses. Excludes process startup."""
    if not TIMEOUTS_S:
        return 0.0
    return sum(TIMEOUTS_S) + PAUSE_S * (len(TIMEOUTS_S) - 1)


def candidate_origin(base_url: str) -> str:
    """Scheme and host only. Rejects non-HTTP URLs so the warmer cannot read local files."""
    parts = urlsplit((base_url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base-url must be an http(s) origin")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def warm_target_urls(base_url: str) -> list[str]:
    """Absolute URLs for ``WARM_PATHS`` on the candidate origin."""
    origin = candidate_origin(base_url)
    return [f"{origin}{path}" for path in WARM_PATHS]


def _outcome(status: int, elapsed_ms: int) -> str:
    """Classify one response. ``redirect`` and ``client`` stop the step."""
    if 300 <= status < 400:
        return "redirect"
    if status == SUCCESS_STATUS and elapsed_ms < TARGET_MS:
        return "warm"
    if status == SUCCESS_STATUS:
        return "slow"
    if status >= 500:
        return "server"
    return "client"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop on the candidate's own response instead of following it off the tag host."""

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


def _request_timeout(attempt_timeout: float, started: float, *, first: bool) -> float:
    """Give the first path the full attempt budget. Later paths share what is left."""
    if first:
        return attempt_timeout
    remaining = attempt_timeout - (time.perf_counter() - started)
    if remaining <= 0:
        raise RuntimeError(f"timed out after {attempt_timeout:g}s")
    return remaining


def _failure(
    *,
    status: int | None,
    latency_ms: int | None,
    attempts: int,
    error: str,
    paths: list[dict[str, object]],
) -> dict[str, object]:
    print(f"ERROR: {error}", file=sys.stderr)
    return {
        "ok": False,
        "status": status,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "error": error,
        "paths": paths,
    }


def warm(base_url: str) -> dict[str, object]:
    """Request the warm paths until each 200 is under ``TARGET_MS``, or the budget is spent."""
    try:
        urls = warm_target_urls(base_url)
    except ValueError as exc:
        return {
            "ok": False,
            "status": None,
            "latency_ms": None,
            "attempts": 0,
            "error": str(exc),
            "paths": [],
        }

    attempts = len(TIMEOUTS_S)
    last_error = "no response"
    last_paths: list[dict[str, object]] = []
    for index, timeout in enumerate(TIMEOUTS_S, start=1):
        started = time.perf_counter()
        results: list[dict[str, object]] = []
        terminal: str | None = None
        blame: dict[str, object] | None = None
        try:
            for path_index, url in enumerate(urls):
                request_timeout = _request_timeout(timeout, started, first=path_index == 0)
                status, elapsed_ms = _fetch(url, request_timeout)
                path = WARM_PATHS[path_index]
                results.append({"path": path, "status": status, "latency_ms": elapsed_ms})
                outcome = _outcome(status, elapsed_ms)
                print(
                    f"warmup attempt {index}/{attempts}: path={path} status={status} "
                    f"elapsed_ms={elapsed_ms} target_ms={TARGET_MS}",
                    file=sys.stderr,
                )
                if outcome == "warm":
                    continue
                terminal = outcome
                blame = results[-1]
                # A slow 200 still means the app did the work. Keep going so the
                # later paths pay their cold start in this same attempt.
                if outcome != "slow":
                    break
        except RuntimeError as exc:
            last_error = str(exc)
            last_paths = results
            print(
                f"warmup attempt {index}/{attempts}: {last_error} timeout_s={timeout:g}",
                file=sys.stderr,
            )
            if index < attempts:
                _pause(PAUSE_S)
            continue

        last_paths = results
        if terminal is None:
            slowest = max(results, key=lambda item: int(item["latency_ms"]))
            summary = ", ".join(
                f"{item['path']} latency_ms={item['latency_ms']} status={item['status']}"
                for item in results
            )
            print(f"candidate warm: {summary}", file=sys.stderr)
            return {
                "ok": True,
                "status": SUCCESS_STATUS,
                "latency_ms": slowest["latency_ms"],
                "attempts": index,
                "error": None,
                "paths": results,
            }

        last = blame if blame is not None else results[-1]
        last_error = f"path={last['path']} status={last['status']} elapsed_ms={last['latency_ms']}"
        if terminal in {"redirect", "client"}:
            if terminal == "redirect":
                message = (
                    f"candidate {last['path']} returned HTTP {last['status']}; "
                    "a redirect is not warm"
                )
            else:
                message = f"candidate {last['path']} returned HTTP {last['status']}; not retrying"
            return _failure(
                status=int(last["status"]),
                latency_ms=int(last["latency_ms"]),
                attempts=index,
                error=message,
                paths=results,
            )
        if index < attempts:
            _pause(PAUSE_S)

    message = (
        f"candidate warm paths did not all respond under {TARGET_MS}ms "
        f"within {attempts} attempts (last: {last_error})"
    )
    return _failure(
        status=None,
        latency_ms=None,
        attempts=attempts,
        error=message,
        paths=last_paths,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args(argv)
    result = warm(args.base_url)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
