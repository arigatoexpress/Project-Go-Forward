#!/usr/bin/env python3
"""Asyncio-based load tester for the THO app.

Target spec (from LAUNCH_READINESS.md):
  20 rps × 5 min, p95 < 1 s, zero 5xx.

Usage examples:
  .venv/bin/python scripts/load_test.py --staging --rps 20 --duration 300
  .venv/bin/python scripts/load_test.py --base-url http://localhost:8080 --rps 1 --duration 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_STAGING_URL = "http://localhost:8080"

PUBLIC_ENDPOINTS = (
    "/health",
    "/healthz/",
    "/api/marketing/inventory-context",
    "/api/appointments/slots",
    "/",
    "/inventory",
)

ADMIN_ENDPOINTS = (
    "/api/admin/check",
    "/api/deals",
    "/api/leads",
    "/api/documents/templates",
)


@dataclass
class _RequestResult:
    status: int
    elapsed_ms: float
    error: bool = False
    is_5xx: bool = False


@dataclass
class LoadTestSummary:
    total_requests: int
    error_count: int
    status_5xx_count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float
    achieved_rps: float
    verdict: str
    failed: bool
    start_time: float
    end_time: float
    duration: float
    target_rps: int
    target_duration: int
    base_url: str
    admin_token: str | None = None


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return d0 + d1


class _LoadTest:
    def __init__(
        self,
        base_url: str,
        rps: int,
        duration: int,
        admin_token: str | None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rps = rps
        self.duration = duration
        self.admin_token = admin_token
        self.results: list[_RequestResult] = []
        self._shutdown = asyncio.Event()
        self._lock = asyncio.Lock()
        self._in_flight: set[asyncio.Task[Any]] = set()
        self._urls = self._build_urls()

    def _build_urls(self) -> list[str]:
        urls = [self.base_url + path for path in PUBLIC_ENDPOINTS]
        if self.admin_token:
            urls.extend(self.base_url + path for path in ADMIN_ENDPOINTS)
        return urls

    async def _do_request(self, client: httpx.AsyncClient, url: str) -> None:
        headers: dict[str, str] = {}
        if self.admin_token:
            headers["X-Admin-Token"] = self.admin_token
        started = time.perf_counter()
        try:
            response = await client.request("GET", url, headers=headers, timeout=30.0)
            elapsed_ms = (time.perf_counter() - started) * 1000
            is_5xx = 500 <= response.status_code < 600
            result = _RequestResult(
                status=response.status_code,
                elapsed_ms=elapsed_ms,
                error=is_5xx,
                is_5xx=is_5xx,
            )
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result = _RequestResult(
                status=0,
                elapsed_ms=elapsed_ms,
                error=True,
                is_5xx=False,
            )
        async with self._lock:
            self.results.append(result)

    async def _spawner(self, client: httpx.AsyncClient) -> None:
        interval = 1.0 / self.rps
        idx = 0
        while not self._shutdown.is_set():
            url = self._urls[idx % len(self._urls)]
            idx += 1
            task = asyncio.create_task(self._do_request(client, url))
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)
            try:
                await asyncio.wait_for(asyncio.sleep(interval), timeout=interval * 2)
            except TimeoutError:
                pass

    def _on_signal(self) -> None:
        self._shutdown.set()

    async def run(self) -> LoadTestSummary:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._on_signal)
            except (NotImplementedError, ValueError):
                pass

        start_time = time.perf_counter()
        limits = httpx.Limits(
            max_connections=max(200, self.rps * 2),
            max_keepalive_connections=max(100, self.rps),
        )
        async with httpx.AsyncClient(limits=limits, http2=False) as client:
            spawner_task = asyncio.create_task(self._spawner(client))
            await asyncio.sleep(self.duration)
            self._shutdown.set()
            spawner_task.cancel()
            try:
                await spawner_task
            except asyncio.CancelledError:
                pass
            if self._in_flight:
                await asyncio.gather(*self._in_flight, return_exceptions=True)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass

        end_time = time.perf_counter()
        total = len(self.results)
        errors = sum(1 for r in self.results if r.error)
        s5xx = sum(1 for r in self.results if r.is_5xx)
        latencies = sorted(r.elapsed_ms for r in self.results if not r.error)
        p50 = _percentile(latencies, 50) if latencies else 0.0
        p95 = _percentile(latencies, 95) if latencies else 0.0
        p99 = _percentile(latencies, 99) if latencies else 0.0
        max_lat = max(latencies) if latencies else 0.0
        mean_lat = sum(latencies) / len(latencies) if latencies else 0.0
        actual_duration = end_time - start_time
        achieved_rps = total / actual_duration if actual_duration > 0 else 0.0

        failed = p95 > 1000 or s5xx > 0
        verdict = "FAIL" if failed else "PASS"

        return LoadTestSummary(
            total_requests=total,
            error_count=errors,
            status_5xx_count=s5xx,
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
            max_ms=max_lat,
            mean_ms=mean_lat,
            achieved_rps=achieved_rps,
            verdict=verdict,
            failed=failed,
            start_time=start_time,
            end_time=end_time,
            duration=actual_duration,
            target_rps=self.rps,
            target_duration=self.duration,
            base_url=self.base_url,
            admin_token=self.admin_token,
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Asyncio load tester for THO")
    parser.add_argument("--base-url", default="", help="Base URL to target")
    parser.add_argument("--rps", type=int, default=20, help="Target requests per second")
    parser.add_argument(
        "--duration", type=int, default=300, help="Test duration in seconds"
    )
    parser.add_argument("--admin-token", default=None, help="Optional admin bearer token")
    parser.add_argument(
        "--staging",
        action="store_true",
        help=f"Use default staging URL ({DEFAULT_STAGING_URL})",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="JSON output path (default: stdout)",
    )
    return parser.parse_args(argv)


def _run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base_url = args.base_url
    if args.staging:
        base_url = DEFAULT_STAGING_URL
    if not base_url:
        print("ERROR: --base-url or --staging required", file=sys.stderr)
        return 1

    if args.rps <= 0:
        print("ERROR: --rps must be > 0", file=sys.stderr)
        return 1
    if args.duration <= 0:
        print("ERROR: --duration must be > 0", file=sys.stderr)
        return 1

    test = _LoadTest(
        base_url=base_url,
        rps=args.rps,
        duration=args.duration,
        admin_token=args.admin_token,
    )
    summary = asyncio.run(test.run())
    data = {
        "p50_ms": round(summary.p50_ms, 2),
        "p95_ms": round(summary.p95_ms, 2),
        "p99_ms": round(summary.p99_ms, 2),
        "max_ms": round(summary.max_ms, 2),
        "mean_ms": round(summary.mean_ms, 2),
        "error_count": summary.error_count,
        "status_5xx_count": summary.status_5xx_count,
        "total_requests": summary.total_requests,
        "achieved_rps": round(summary.achieved_rps, 2),
        "verdict": summary.verdict,
        "failed": summary.failed,
        "target_rps": summary.target_rps,
        "duration_s": round(summary.duration, 2),
        "base_url": summary.base_url,
    }
    json_out = json.dumps(data, indent=2, sort_keys=True)
    if args.output == "-":
        print(json_out)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(json_out)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
