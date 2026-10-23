"""Candidate load-sanity guards. These tests do not open a network connection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scripts.candidate_load_sanity import (
    DEFAULT_CANDIDATE_URL,
    DEFAULT_CONCURRENCY,
    DEFAULT_DURATION_S,
    DEFAULT_TARGET_RPS,
    MAX_CONCURRENCY,
    MAX_DURATION_S,
    MAX_TARGET_RPS,
    MAX_UNRECOVERED_5XX,
    P95_BUDGET_MS,
    Response,
    TargetRejected,
    TransportError,
    attempt_get,
    candidate_origin,
    check_limits,
    is_gateway_html,
    percentile,
    read_paths,
    run_load,
    slots_query_date,
)

ROOT = Path(__file__).resolve().parents[1]
GFE_HTML = (
    b"\n<html><head><title>502 Server Error</title></head>"
    b"<body>Please try again in 30 seconds.</body></html>\n"
)
CANDIDATE = DEFAULT_CANDIDATE_URL


def test_defaults_match_the_documented_budget():
    assert P95_BUDGET_MS == 1000
    assert MAX_UNRECOVERED_5XX == 0
    assert DEFAULT_DURATION_S <= 90
    assert DEFAULT_DURATION_S < 120
    assert DEFAULT_CONCURRENCY <= MAX_CONCURRENCY == 4
    assert DEFAULT_TARGET_RPS <= MAX_TARGET_RPS == 4
    assert MAX_DURATION_S <= 90


def test_candidate_origin_accepts_the_tag_host_and_loopback():
    assert candidate_origin(CANDIDATE) == CANDIDATE
    assert candidate_origin(CANDIDATE + "/healthz/") == CANDIDATE
    assert candidate_origin("") == CANDIDATE
    assert candidate_origin("http://127.0.0.1:8080/staff") == "http://127.0.0.1:8080"
    assert candidate_origin("http://localhost:8080") == "http://localhost:8080"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.texashomeoutlet.com",
        "https://texashomeoutlet.com/inventory",
        "https://tho.sapphirealpha.xyz/staff",
        "https://project-go-forward-trgi34bxuq-uc.a.run.app",
        "http://candidate---project-go-forward-trgi34bxuq-uc.a.run.app",
        "https://user@candidate---project-go-forward-trgi34bxuq-uc.a.run.app",
        "https://candidate.texashomeoutlet.com",
        "https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app\nEVIL=1",
    ],
)
def test_candidate_origin_refuses_live_and_untagged_hosts(url):
    with pytest.raises(TargetRejected):
        candidate_origin(url)


def test_limits_reject_a_longer_or_heavier_run():
    check_limits(DEFAULT_DURATION_S, DEFAULT_CONCURRENCY, DEFAULT_TARGET_RPS, 10)
    with pytest.raises(TargetRejected):
        check_limits(MAX_DURATION_S + 1, 1, 1, 10)
    with pytest.raises(TargetRejected):
        check_limits(30, MAX_CONCURRENCY + 1, 1, 10)
    with pytest.raises(TargetRejected):
        check_limits(30, 1, MAX_TARGET_RPS + 0.1, 10)


def test_read_paths_stay_on_candidate_surfaces():
    paths = read_paths("2026-10-26")
    assert paths == (
        "/healthz/",
        "/staff",
        "/sitemap.xml",
        "/api/marketing/inventory-context",
        "/api/appointments/slots?date=2026-10-26",
    )
    assert "/" not in paths
    assert "/inventory" not in paths
    assert "/contact" not in paths
    assert "/appointments" not in paths


def test_slots_date_skips_sunday():
    assert slots_query_date(date(2026, 10, 23)) == "2026-10-26"
    assert slots_query_date(date(2026, 10, 22)) == "2026-10-24"


def test_percentile_interpolates():
    assert percentile([10, 20, 30, 40, 50], 50) == 30
    assert percentile([1000], 95) == 1000
    assert percentile([], 95) == 0


def test_gateway_html_matches_the_frontend_502_page():
    assert is_gateway_html(502, "text/html; charset=UTF-8", GFE_HTML)
    assert not is_gateway_html(200, "application/json", b'{"status":"ok"}')
    assert not is_gateway_html(500, "application/json", b'{"error":"nope"}')


def test_attempt_retries_gateway_html_and_keeps_the_successful_latency():
    calls = {"n": 0}
    pauses: list[float] = []

    def fetch(origin, path, timeout):
        calls["n"] += 1
        assert origin == CANDIDATE
        assert "texashomeoutlet.com" not in origin
        if calls["n"] == 1:
            return Response(502, GFE_HTML, "text/html; charset=UTF-8", "", 900)
        return Response(200, b"ok", "text/html", "", 40)

    sample = attempt_get(
        CANDIDATE,
        "/staff",
        fetch=fetch,
        sleep=pauses.append,
        timeout=1,
    )

    assert calls["n"] == 2
    assert pauses == [0.5]
    assert sample.ok
    assert sample.elapsed_ms == 40
    assert sample.recovered_gateway_html
    assert sample.unrecovered_5xx is False


def test_persistent_gateway_html_counts_as_unrecovered_5xx():
    def fetch(origin, path, timeout):
        return Response(502, GFE_HTML, "text/html; charset=UTF-8", "", 20)

    sample = attempt_get(
        CANDIDATE, "/healthz/", fetch=fetch, sleep=lambda _seconds: None, timeout=1
    )

    assert sample.unrecovered_5xx
    assert sample.ok is False


def test_redirect_to_the_live_site_is_not_followed():
    requested: list[tuple[str, str]] = []

    def fetch(origin, path, timeout):
        requested.append((origin, path))
        return Response(308, b"", "text/html", "https://www.texashomeoutlet.com/inventory", 5)

    sample = attempt_get(CANDIDATE, "/staff", fetch=fetch, sleep=lambda _seconds: None, timeout=1)

    assert sample.redirect
    assert sample.detail == "https://www.texashomeoutlet.com/inventory"
    assert requested == [(CANDIDATE, "/staff")]


def test_client_error_is_not_retried():
    calls = {"n": 0}

    def fetch(origin, path, timeout):
        calls["n"] += 1
        return Response(404, b"missing", "text/plain", "", 3)

    sample = attempt_get(CANDIDATE, "/staff", fetch=fetch, sleep=lambda _seconds: None, timeout=1)

    assert calls["n"] == 1
    assert sample.unexpected_status


def test_transport_error_retries_then_fails():
    calls = {"n": 0}

    def fetch(origin, path, timeout):
        calls["n"] += 1
        raise TransportError("connection reset")

    sample = attempt_get(
        CANDIDATE, "/healthz/", fetch=fetch, sleep=lambda _seconds: None, timeout=1
    )

    assert calls["n"] == 3
    assert sample.transport_error


def test_run_load_fails_closed_when_warmup_redirects():
    def fetch(origin, path, timeout):
        return Response(308, b"", "text/html", "https://www.texashomeoutlet.com/", 4)

    report = run_load(
        CANDIDATE,
        ("/healthz/",),
        duration_s=30,
        concurrency=2,
        target_rps=2,
        fetch=fetch,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0,
        timeout=1,
        slots_date="2026-10-26",
    )

    assert report["verdict"] == "FAIL"
    assert report["warmup_failed"] is True
    assert report["total_requests"] == 0
    assert report["measured_against"] == "candidate-tag-url"


def test_run_load_passes_under_the_p95_budget_with_zero_5xx():
    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += seconds

    def fetch(origin, path, timeout):
        return Response(200, b'{"status":"ok","version":"abc123"}', "application/json", "", 25)

    report = run_load(
        CANDIDATE,
        ("/healthz/", "/staff"),
        duration_s=1,
        concurrency=1,
        target_rps=2,
        fetch=fetch,
        sleep=sleep,
        monotonic=monotonic,
        timeout=1,
        slots_date="2026-10-26",
    )

    assert report["verdict"] == "PASS"
    assert report["status_5xx_count"] == 0
    assert report["p95_ms"] < P95_BUDGET_MS
    assert report["p50_ms"] == 25
    assert report["healthz_version"] == "abc123"
    assert report["total_requests"] >= 1


def test_p95_at_the_budget_fails():
    def fetch(origin, path, timeout):
        return Response(200, b"ok", "text/plain", "", P95_BUDGET_MS)

    report = run_load(
        "http://127.0.0.1:8080",
        ("/healthz/",),
        duration_s=1,
        concurrency=1,
        target_rps=1,
        fetch=fetch,
        sleep=lambda seconds: None,
        monotonic=_StopAfter(1),
        timeout=1,
        slots_date="2026-10-26",
    )

    assert report["p95_ms"] == P95_BUDGET_MS
    assert report["verdict"] == "FAIL"
    assert report["measured_against"] == "loopback"


class _StopAfter:
    """A clock that ends the window after one sample plus the following sleep."""

    def __init__(self, duration_s: float):
        self.duration_s = duration_s
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.calls <= 2:
            return 0.0
        return self.duration_s


def test_cli_refuses_the_live_domain_without_a_request(monkeypatch):
    def explode(origin, path, timeout):
        raise AssertionError("live host must be rejected before fetch")

    monkeypatch.setattr("scripts.candidate_load_sanity.fetch_once", explode)
    from scripts.candidate_load_sanity import _run

    assert _run(["--base-url", "https://www.texashomeoutlet.com"]) == 2


def test_workflow_is_manual_and_stays_on_the_candidate_tag():
    text = (ROOT / ".github/workflows/candidate-gauntlet.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "\n  push:" not in text
    assert "candidate_origin" in text
    assert "scripts/candidate_load_sanity.py" in text
    assert "id-token:" not in text
    assert "${{ secrets" not in text
    assert "update-traffic" not in text
    assert "www.texashomeoutlet.com" not in text.split("jobs:", 1)[0]


def test_playwright_target_uses_the_same_candidate_url():
    text = (ROOT / "e2e/gauntlet/target.mjs").read_text(encoding="utf-8")
    assert DEFAULT_CANDIDATE_URL in text


def test_playwright_target_rejects_live_and_accepts_candidate():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = """
import { candidateOrigin } from './e2e/gauntlet/target.mjs';
const accepted = candidateOrigin(process.env.CANDIDATE_URL);
if (accepted !== process.env.CANDIDATE_URL) {
  throw new Error(`origin mismatch ${accepted}`);
}
for (const blocked of [
  'https://www.texashomeoutlet.com',
  'https://texashomeoutlet.com/inventory',
  'https://project-go-forward-trgi34bxuq-uc.a.run.app',
]) {
  let rejected = false;
  try { candidateOrigin(blocked); } catch { rejected = true; }
  if (!rejected) throw new Error(`accepted ${blocked}`);
}
console.log(JSON.stringify({ ok: true }));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CANDIDATE_URL": CANDIDATE},
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True
