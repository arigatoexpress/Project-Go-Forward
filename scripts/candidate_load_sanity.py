#!/usr/bin/env python3
"""Short read-only load sanity check for a Cloud Run candidate tag URL.

The Deploy workflow publishes a revision at 0% traffic with the tag
``candidate``. This script sends a modest GET load to that tag URL only.
It refuses the live storefront and the untagged service URL, which is where
customer traffic is pinned.

Thresholds (also documented in docs/CANDIDATE_GAUNTLET.md):

- p95 under 1000 ms. Equal to 1000 ms fails.
- unrecovered HTTP 5xx count is 0.

p50 and p95 use the timed window after one warmup GET per path. Latency is
the successful attempt only. A Google frontend HTML 502, other HTTP 5xx, or
a transport error is retried up to three times with the same short backoff
as ``scripts/production_smoke.py``. A recovered response is logged and left
out of the 5xx count and the percentile sample. A response that is still
failing after those attempts fails the run. Redirects are not followed.

Defaults stay under two minutes: 30 seconds, 2 workers, about 2 requests per
second. The CLI rejects anything above 90 seconds, 4 workers, or 4 requests
per second.

Public HTML paths (``/``, ``/inventory``, ``/contact``, ``/appointments``)
are not requested. On the candidate host those paths answer 308 and point a
client at https://www.texashomeoutlet.com. The browser gauntlet covers those
journeys without following that redirect.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DEFAULT_CANDIDATE_URL = "https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app"
P95_BUDGET_MS = 1000.0
MAX_UNRECOVERED_5XX = 0
DEFAULT_DURATION_S = 30
MAX_DURATION_S = 90
DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 4
DEFAULT_TARGET_RPS = 2.0
MAX_TARGET_RPS = 4.0
MAX_TIMEOUT_S = 15.0
DEFAULT_TIMEOUT_S = 10.0
TRANSIENT_ATTEMPTS = 3
TRANSIENT_BACKOFF_SECONDS = (0.5, 1.5)
MAX_BODY_BYTES = 8 * 1024 * 1024
STORE_TZ = ZoneInfo("America/Chicago")

LIVE_HOSTS = frozenset(
    {
        "www.texashomeoutlet.com",
        "texashomeoutlet.com",
        "tho.sapphirealpha.xyz",
    }
)

Fetch = Callable[[str, str, float], "Response"]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


class TargetRejected(ValueError):
    """The operator asked for a target or a budget this script will not run."""


class TransportError(Exception):
    """A timeout or connection failure before an HTTP status arrived."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str
    location: str
    elapsed_ms: float


@dataclass
class Sample:
    path: str
    status: int
    elapsed_ms: float
    unrecovered_5xx: bool = False
    transport_error: bool = False
    redirect: bool = False
    unexpected_status: bool = False
    recovered_gateway_html: bool = False
    recovered_5xx: bool = False
    detail: str = ""
    body: bytes = field(default=b"", repr=False)

    @property
    def ok(self) -> bool:
        return not (
            self.unrecovered_5xx or self.transport_error or self.redirect or self.unexpected_status
        )


def _hostname(value: str) -> str:
    return (value or "").lower().rstrip(".")


def is_live_host(hostname: str) -> bool:
    host = _hostname(hostname)
    return host in LIVE_HOSTS or host.endswith(".texashomeoutlet.com")


def candidate_origin(base_url: str | None) -> str:
    """Return the origin to load, or raise TargetRejected.

    An empty value uses the stable candidate tag URL. Loopback is allowed so
    tests and a local server can exercise the same script. Any other host,
    including the live domain and the untagged ``*.run.app`` service URL, is
    refused before a socket opens.
    """
    raw = (base_url or "").strip() or DEFAULT_CANDIDATE_URL
    parts = urlsplit(raw)
    host = _hostname(parts.hostname or "")
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise TargetRejected("BASE_URL must be an HTTP(S) URL without credentials")
    if is_live_host(host):
        raise TargetRejected(
            "Refusing the live storefront. The load check runs only against the candidate tag host."
        )
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    candidate = host.startswith("candidate---") and host.endswith(".run.app")
    if not loopback and not candidate:
        raise TargetRejected(
            "BASE_URL must be the Cloud Run candidate tag host (candidate---*.run.app) or a loopback host."
        )
    if candidate and parts.scheme != "https":
        raise TargetRejected("The candidate tag URL must use https.")
    origin = f"{parts.scheme}://{parts.netloc}"
    if any(character.isspace() for character in origin):
        raise TargetRejected("BASE_URL must not contain whitespace")
    return origin


def check_limits(duration_s: float, concurrency: int, target_rps: float, timeout_s: float) -> None:
    if duration_s < 1 or duration_s > MAX_DURATION_S:
        raise TargetRejected(f"duration must be from 1 to {MAX_DURATION_S} seconds")
    if concurrency < 1 or concurrency > MAX_CONCURRENCY:
        raise TargetRejected(f"concurrency must be from 1 to {MAX_CONCURRENCY}")
    if target_rps <= 0 or target_rps > MAX_TARGET_RPS:
        raise TargetRejected(f"rps must be greater than 0 and at most {MAX_TARGET_RPS}")
    if timeout_s <= 0 or timeout_s > MAX_TIMEOUT_S:
        raise TargetRejected(f"timeout must be greater than 0 and at most {MAX_TIMEOUT_S} seconds")


def slots_query_date(today: date | None = None) -> str:
    """A near weekday inside the booking window. Sundays are closed."""
    current = today or datetime.now(STORE_TZ).date()
    chosen = current + timedelta(days=2)
    while chosen.weekday() == 6:
        chosen += timedelta(days=1)
    return chosen.isoformat()


def read_paths(slots_date: str) -> tuple[str, ...]:
    """GET paths the candidate answers itself. None of these 308 to the live site."""
    return (
        "/healthz/",
        "/staff",
        "/sitemap.xml",
        "/api/marketing/inventory-context",
        f"/api/appointments/slots?date={slots_date}",
    )


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return sorted_values[int(k)]
    return sorted_values[lower] * (upper - k) + sorted_values[upper] * (k - lower)


def is_gateway_html(status: int, content_type: str, body: bytes) -> bool:
    """True for the stock Google frontend HTML error page and other HTML 5xx."""
    if status < 500:
        return False
    snippet = body[:400].decode("utf-8", "replace").lower()
    if "502 server error" in snippet or "please try again in 30 seconds" in snippet:
        return True
    return "text/html" in content_type.lower()


def _backoff_after(attempt: int) -> float:
    index = min(attempt - 1, len(TRANSIENT_BACKOFF_SECONDS) - 1)
    return TRANSIENT_BACKOFF_SECONDS[index]


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def attempt_get(
    origin: str,
    path: str,
    *,
    fetch: Fetch,
    sleep: Sleep,
    timeout: float,
    attempts: int = TRANSIENT_ATTEMPTS,
) -> Sample:
    """GET ``path`` once, retrying transport errors and HTTP 5xx.

    The returned elapsed time is the attempt that counted. Redirects are
    returned as failures and are not requested again.
    """
    saw_gateway = False
    saw_5xx = False
    detail = ""
    for attempt in range(1, attempts + 1):
        try:
            response = fetch(origin, path, timeout)
        except TransportError as exc:
            detail = str(exc)
            if attempt >= attempts:
                return Sample(path, 0, 0.0, transport_error=True, detail=detail)
            _log(f"load transient attempt {attempt}/{attempts}: {path} {detail}")
            sleep(_backoff_after(attempt))
            continue
        if response.status >= 500:
            saw_5xx = True
            gateway = is_gateway_html(response.status, response.content_type, response.body)
            saw_gateway = saw_gateway or gateway
            detail = (
                f"status={response.status} content_type={response.content_type!r} "
                f"gateway_html={gateway}"
            )
            if attempt >= attempts:
                _log(f"load persistent failure: {path} {detail}")
                return Sample(
                    path,
                    response.status,
                    response.elapsed_ms,
                    unrecovered_5xx=True,
                    detail=detail,
                )
            _log(f"load transient attempt {attempt}/{attempts}: {path} {detail}")
            sleep(_backoff_after(attempt))
            continue
        if 300 <= response.status < 400:
            return Sample(
                path,
                response.status,
                response.elapsed_ms,
                redirect=True,
                detail=response.location,
            )
        if response.status != 200:
            return Sample(
                path,
                response.status,
                response.elapsed_ms,
                unexpected_status=True,
                detail=f"status={response.status}",
            )
        if attempt > 1:
            _log(f"load recovered {path} status=200 after {attempt} attempts")
        body = response.body if path.startswith("/healthz") else b""
        return Sample(
            path,
            200,
            response.elapsed_ms,
            recovered_gateway_html=saw_gateway,
            recovered_5xx=saw_5xx,
            body=body,
        )
    return Sample(path, 0, 0.0, transport_error=True, detail=detail or "no response")


def _round(value: float) -> float:
    return round(value, 2)


def _percentile_latencies(samples: list[Sample]) -> list[float]:
    """Successful attempts, excluding ones that recovered from an HTTP 5xx.

    ``attempt_get`` leaves ``sample.ok`` true after a 5xx that later returns
    200. Those samples stay in ``recovered_5xx_count`` and
    ``recovered_gateway_html_count`` and out of p50/p95. See
    docs/CANDIDATE_GAUNTLET.md.
    """
    return sorted(
        sample.elapsed_ms
        for sample in samples
        if sample.ok and not sample.recovered_5xx and not sample.recovered_gateway_html
    )


def _path_stats(samples: list[Sample]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.path, []).append(sample)
    stats: dict[str, dict[str, float | int]] = {}
    for path, group in grouped.items():
        latencies = _percentile_latencies(group)
        stats[path] = {
            "count": len(group),
            "success_count": len(latencies),
            "p50_ms": _round(percentile(latencies, 50)),
            "p95_ms": _round(percentile(latencies, 95)),
            "max_ms": _round(max(latencies) if latencies else 0.0),
            "status_5xx_count": sum(1 for sample in group if sample.unrecovered_5xx),
        }
    return stats


def _healthz_version(samples: list[Sample]) -> str:
    for sample in samples:
        if sample.path.startswith("/healthz") and sample.body:
            try:
                payload = json.loads(sample.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            version = payload.get("version") if isinstance(payload, dict) else ""
            if isinstance(version, str):
                return version
    return ""


def summarize(
    origin: str,
    samples: list[Sample],
    warmup: list[Sample],
    *,
    duration_s: float,
    concurrency: int,
    target_rps: float,
    slots_date: str,
    warmup_failed: bool,
) -> dict[str, object]:
    latencies = _percentile_latencies(samples)
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    status_5xx = sum(1 for sample in samples if sample.unrecovered_5xx)
    transport_errors = sum(1 for sample in samples if sample.transport_error)
    redirects = sum(1 for sample in samples if sample.redirect)
    unexpected = sum(1 for sample in samples if sample.unexpected_status)
    recovered_gateway = sum(1 for sample in samples if sample.recovered_gateway_html)
    recovered_5xx = sum(1 for sample in samples if sample.recovered_5xx)
    # A recovered 5xx is still a success. Fail closed only when nothing
    # succeeded, not when the percentile sample is empty because every
    # success had to recover.
    succeeded = any(sample.ok for sample in samples)
    failed = (
        warmup_failed
        or status_5xx > MAX_UNRECOVERED_5XX
        or transport_errors > 0
        or redirects > 0
        or unexpected > 0
        or not succeeded
        or p95 >= P95_BUDGET_MS
    )
    return {
        "schema_version": 1,
        "base_url": origin,
        "measured_against": (
            "candidate-tag-url"
            if _hostname(urlsplit(origin).hostname or "").startswith("candidate---")
            else "loopback"
        ),
        "healthz_version": _healthz_version(warmup),
        "slots_date": slots_date,
        "duration_s": duration_s,
        "concurrency": concurrency,
        "target_rps": target_rps,
        "total_requests": len(samples),
        "success_count": len(latencies),
        "p50_ms": _round(p50),
        "p95_ms": _round(p95),
        "max_ms": _round(max(latencies) if latencies else 0.0),
        "status_5xx_count": status_5xx,
        "recovered_gateway_html_count": recovered_gateway,
        "recovered_5xx_count": recovered_5xx,
        "transport_error_count": transport_errors,
        "redirect_count": redirects,
        "unexpected_status_count": unexpected,
        "warmup_failed": warmup_failed,
        "warmup": [
            {
                "path": sample.path,
                "status": sample.status,
                "elapsed_ms": _round(sample.elapsed_ms),
                "ok": sample.ok,
                "detail": sample.detail,
            }
            for sample in warmup
        ],
        "per_path": _path_stats(samples),
        "thresholds": {
            "p95_ms_max_exclusive": P95_BUDGET_MS,
            "status_5xx_max": MAX_UNRECOVERED_5XX,
            "max_duration_s": MAX_DURATION_S,
            "max_concurrency": MAX_CONCURRENCY,
            "max_target_rps": MAX_TARGET_RPS,
        },
        "verdict": "FAIL" if failed else "PASS",
        "failed": failed,
    }


def run_load(
    origin: str,
    paths: tuple[str, ...] | list[str],
    *,
    duration_s: float,
    concurrency: int,
    target_rps: float,
    fetch: Fetch,
    sleep: Sleep,
    monotonic: Clock,
    timeout: float,
    slots_date: str,
) -> dict[str, object]:
    warmup = [
        attempt_get(origin, path, fetch=fetch, sleep=sleep, timeout=timeout) for path in paths
    ]
    if any(not sample.ok for sample in warmup):
        return summarize(
            origin,
            [],
            warmup,
            duration_s=duration_s,
            concurrency=concurrency,
            target_rps=target_rps,
            slots_date=slots_date,
            warmup_failed=True,
        )

    samples: list[Sample] = []
    lock = threading.Lock()
    gap_s = concurrency / target_rps
    deadline = monotonic() + duration_s

    def worker(worker_id: int) -> None:
        index = worker_id
        while monotonic() < deadline:
            path = paths[index % len(paths)]
            index += concurrency
            sample = attempt_get(origin, path, fetch=fetch, sleep=sleep, timeout=timeout)
            with lock:
                samples.append(sample)
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(gap_s, remaining))

    threads = [
        threading.Thread(target=worker, args=(worker_id,), name=f"load-{worker_id}")
        for worker_id in range(concurrency)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return summarize(
        origin,
        samples,
        warmup,
        duration_s=duration_s,
        concurrency=concurrency,
        target_rps=target_rps,
        slots_date=slots_date,
        warmup_failed=False,
    )


def fetch_once(origin: str, path: str, timeout: float) -> Response:
    """One GET. Redirects are returned as the 3xx and are not followed."""
    parts = urlsplit(origin)
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, timeout=timeout
        )
    elif parts.scheme == "http":
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    else:
        raise TransportError(f"unsupported scheme {parts.scheme}")
    started = time.perf_counter()
    try:
        connection.request(
            "GET",
            path,
            headers={
                "User-Agent": "tho-candidate-load-sanity/1.0",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        body = response.read(MAX_BODY_BYTES)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return Response(
            status=response.status,
            body=body,
            content_type=response.getheader("Content-Type") or "",
            location=response.getheader("Location") or "",
            elapsed_ms=elapsed_ms,
        )
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        raise TransportError(f"{path} network error: {exc}") from exc
    finally:
        connection.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Candidate-tag load sanity check")
    parser.add_argument(
        "--base-url",
        default="",
        help=f"Candidate tag origin (default: {DEFAULT_CANDIDATE_URL})",
    )
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--rps", type=float, default=DEFAULT_TARGET_RPS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--output",
        default="-",
        help="JSON report path. '-' prints it to stdout (always printed as well).",
    )
    return parser.parse_args(argv)


def _print_report(report: dict[str, object], output: str) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True)
    _log(
        "candidate load sanity "
        f"verdict={report['verdict']} measured_against={report['measured_against']} "
        f"p50_ms={report['p50_ms']} p95_ms={report['p95_ms']} "
        f"status_5xx_count={report['status_5xx_count']} "
        f"recovered_gateway_html_count={report['recovered_gateway_html_count']} "
        f"total_requests={report['total_requests']} healthz_version={report['healthz_version']}"
    )
    if output != "-":
        Path(output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


def _run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        origin = candidate_origin(args.base_url)
        check_limits(args.duration, args.concurrency, args.rps, args.timeout)
    except TargetRejected as exc:
        _log(f"ERROR: {exc}")
        return 2
    slots_date = slots_query_date()
    report = run_load(
        origin,
        read_paths(slots_date),
        duration_s=args.duration,
        concurrency=args.concurrency,
        target_rps=args.rps,
        fetch=fetch_once,
        sleep=time.sleep,
        monotonic=time.perf_counter,
        timeout=args.timeout,
        slots_date=slots_date,
    )
    _print_report(report, args.output)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(_run())
