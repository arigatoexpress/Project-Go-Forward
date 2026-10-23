"""Candidate warm-up bounds, and the deploy step that runs it before smoke."""

import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scripts import production_smoke, warm_candidate

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml"


def test_worst_case_is_thirty_five_seconds():
    assert warm_candidate.TIMEOUTS_S == (20.0, 8.0, 5.0)
    assert warm_candidate.PAUSE_S == 1.0
    assert warm_candidate.TARGET_MS == 1500
    assert warm_candidate.worst_case_seconds() == 35


def test_fast_redirect_warms_on_the_first_attempt(monkeypatch):
    calls = []
    pauses = []

    def fake_fetch(url, timeout):
        calls.append((url, timeout))
        return 308, 80

    monkeypatch.setattr(warm_candidate, "_fetch", fake_fetch)
    monkeypatch.setattr(warm_candidate, "_pause", lambda seconds: pauses.append(seconds))

    result = warm_candidate.warm("https://candidate.example.test/ignored")

    assert result["ok"] is True
    assert result["status"] == 308
    assert result["latency_ms"] == 80
    assert result["attempts"] == 1
    assert calls == [("https://candidate.example.test/", 20.0)]
    assert pauses == []


def test_slow_cold_start_then_fast_response_is_warm(monkeypatch):
    calls = []
    pauses = []
    responses = [(308, 9000), (308, 120)]

    def fake_fetch(url, timeout):
        calls.append(timeout)
        return responses.pop(0)

    monkeypatch.setattr(warm_candidate, "_fetch", fake_fetch)
    monkeypatch.setattr(warm_candidate, "_pause", lambda seconds: pauses.append(seconds))

    result = warm_candidate.warm("https://candidate.example.test")

    assert result["ok"] is True
    assert result["latency_ms"] == 120
    assert result["attempts"] == 2
    assert calls == [20.0, 8.0]
    assert pauses == [1.0]


def test_server_error_is_retried_until_a_fast_success(monkeypatch):
    responses = [(503, 40), (200, 90)]

    def fake_fetch(url, timeout):
        return responses.pop(0)

    monkeypatch.setattr(warm_candidate, "_fetch", fake_fetch)
    monkeypatch.setattr(warm_candidate, "_pause", lambda _seconds: None)

    result = warm_candidate.warm("https://candidate.example.test")

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["latency_ms"] == 90
    assert result["attempts"] == 2


def test_dead_candidate_stops_at_the_attempt_budget(monkeypatch):
    calls = []
    pauses = []

    def fake_fetch(url, timeout):
        calls.append(timeout)
        raise RuntimeError(f"timed out after {timeout:g}s")

    monkeypatch.setattr(warm_candidate, "_fetch", fake_fetch)
    monkeypatch.setattr(warm_candidate, "_pause", lambda seconds: pauses.append(seconds))

    result = warm_candidate.warm("https://candidate.example.test")

    assert result["ok"] is False
    assert result["attempts"] == 3
    assert calls == [20.0, 8.0, 5.0]
    assert pauses == [1.0, 1.0]
    assert "timed out after 5s" in result["error"]
    assert str(warm_candidate.TARGET_MS) in result["error"]


def test_client_error_fails_without_burning_the_budget(monkeypatch):
    calls = []
    pauses = []

    def fake_fetch(url, timeout):
        calls.append(timeout)
        return 404, 30

    monkeypatch.setattr(warm_candidate, "_fetch", fake_fetch)
    monkeypatch.setattr(warm_candidate, "_pause", lambda seconds: pauses.append(seconds))

    result = warm_candidate.warm("https://candidate.example.test")

    assert result["ok"] is False
    assert result["status"] == 404
    assert result["attempts"] == 1
    assert calls == [20.0]
    assert pauses == []


def test_responses_that_stay_slow_fail_when_the_budget_ends(monkeypatch):
    calls = []

    def fake_fetch(url, timeout):
        calls.append(timeout)
        return 308, 4000

    monkeypatch.setattr(warm_candidate, "_fetch", fake_fetch)
    monkeypatch.setattr(warm_candidate, "_pause", lambda _seconds: None)

    result = warm_candidate.warm("https://candidate.example.test")

    assert result["ok"] is False
    assert calls == [20.0, 8.0, 5.0]
    assert "elapsed_ms=4000" in result["error"]


@pytest.mark.parametrize("base_url", ["", "file:///etc/passwd", "ftp://example.test/", "not a url"])
def test_non_http_base_url_is_rejected(base_url):
    result = warm_candidate.warm(base_url)

    assert result["ok"] is False
    assert result["attempts"] == 0
    assert "http(s)" in result["error"]


def test_fetch_does_not_follow_a_redirect():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(308)
            self.send_header("Location", "http://127.0.0.1:9/should-not-fetch")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = warm_candidate.warm(f"http://127.0.0.1:{port}/extra")
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result["ok"] is True
    assert result["status"] == 308
    assert result["latency_ms"] < warm_candidate.TARGET_MS
    assert seen == ["/"]


def test_main_exits_zero_only_when_warm(monkeypatch, capsys):
    monkeypatch.setattr(
        warm_candidate,
        "warm",
        lambda _url: {
            "ok": True,
            "status": 308,
            "latency_ms": 90,
            "attempts": 2,
            "error": None,
        },
    )

    assert warm_candidate.main(["--base-url", "https://candidate.example.test"]) == 0

    monkeypatch.setattr(
        warm_candidate,
        "warm",
        lambda _url: {
            "ok": False,
            "status": None,
            "latency_ms": None,
            "attempts": 3,
            "error": "cold",
        },
    )

    assert warm_candidate.main(["--base-url", "https://candidate.example.test"]) == 1
    assert capsys.readouterr().out == ""


def _job_steps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = workflow["jobs"]["build-and-deploy"]
    return job, job["steps"]


def test_warmup_step_runs_before_verify_without_changing_permissions():
    job, steps = _job_steps()

    assert job["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "statuses": "write",
    }
    names = [step.get("name") for step in steps]
    assert names.index("Warm candidate") < names.index("Verify deployment")
    warm = next(step for step in steps if step.get("name") == "Warm candidate")
    verify = next(step for step in steps if step.get("name") == "Verify deployment")
    assert "permissions" not in warm
    assert "scripts/warm_candidate.py" in warm["run"]
    assert "tag:candidate" in warm["run"]
    assert "update-traffic" not in warm["run"]
    assert "update-traffic" not in verify["run"]
    assert "--no-traffic" in next(
        step["run"] for step in steps if step.get("name") == "Build and deploy to Cloud Run"
    )


def _run_warm_step(*, url: str, python_exit: int = 0):
    _job, steps = _job_steps()
    step = next(step for step in steps if step.get("name") == "Warm candidate")["run"]
    step = re.sub(r"\$\{\{[^}]+\}\}", "smoke", step)
    mocks = f"""
gcloud() {{ printf '%s\\n' '{url}'; }}
python() {{ printf '%s\\n' "$*"; return {python_exit}; }}
"""
    return subprocess.run(
        ["bash", "-c", mocks + step],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_warm_step_invokes_the_script_with_the_candidate_url():
    result = _run_warm_step(url="https://candidate.example.test")

    assert result.returncode == 0, result.stderr
    assert "Candidate URL: https://candidate.example.test" in result.stdout
    assert "scripts/warm_candidate.py --base-url https://candidate.example.test" in result.stdout


def test_warm_step_fails_fast_when_the_candidate_url_is_missing():
    result = _run_warm_step(url="")

    assert result.returncode != 0
    assert "before warm-up" in result.stdout
    assert "warm_candidate.py" not in result.stdout


def test_warm_step_fails_when_the_warmer_fails():
    result = _run_warm_step(url="https://candidate.example.test", python_exit=1)

    assert result.returncode != 0
    assert "scripts/warm_candidate.py --base-url https://candidate.example.test" in result.stdout


def test_smoke_reports_root_latency(monkeypatch, capsys):
    def fake_read_url(base_url, path, *, timeout):
        if path.startswith("/api/"):
            return 401, b'{"error":"unauthorized"}', "application/json", 15
        if path == "/robots.txt":
            body = b"User-agent: *\nSitemap: https://example.test/sitemap.xml\n"
            return 200, body, "text/plain", 4
        if path == "/sitemap.xml":
            body = b"<urlset><url><loc>https://example.test/</loc></url></urlset>"
            return 200, body, "application/xml", 4
        elapsed = 420 if path == "/" else 30
        return 200, b'<html><body><div id="root"></div></body></html>', "text/html", elapsed

    def fake_json_probe(base_url, path, *, timeout):
        if path in {"/health", "/healthz/"}:
            return 200, {"status": "ok", "version": "abc", "uptime_s": 1}, 5
        if "inventory-context" in path:
            return 200, {"success": True, "homes": []}, 5
        if "slots" in path:
            return 200, {"date": "2026-10-24", "available_slots": ["9:00"]}, 5
        if "passkey/status" in path:
            return (
                200,
                {
                    "enabled": True,
                    "persistent": True,
                    "rp_ids": ["sapphirealpha.xyz", "texashomeoutlet.com"],
                },
                5,
            )
        return 200, {"status": "ok"}, 5

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)
    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    result = production_smoke.run_smoke("https://example.test", timeout=1.0, min_homes=0)

    assert result["root_latency_ms"] == 420
    root = next(probe for probe in result["probes"] if probe["name"] == "/")
    assert root["elapsed_ms"] == 420
    assert "latency_ms=420" in root["evidence"]

    production_smoke.main(["--base-url", "https://example.test", "--min-homes", "0"])
    captured = capsys.readouterr()
    assert "GET / latency_ms=420" in captured.err
    assert '"root_latency_ms": 420' in captured.out
