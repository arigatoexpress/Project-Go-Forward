"""Tests for scripts/live_monitoring_check.py.

The monitoring script is intentionally read-only. These tests mock the network
layer and assert on probe coverage, response-shape validation, and ops warnings.
"""
from __future__ import annotations

import json
from typing import Any

from scripts import live_monitoring_check as lmc


def test_default_base_url_targets_tho_subdomain():
    assert lmc.DEFAULT_BASE_URL == "https://tho.sapphirealpha.xyz"


def test_default_lead_backlog_threshold_matches_readiness_context():
    assert lmc.DEFAULT_LEAD_BACKLOG_THRESHOLD == 50


class _FakeGet:
    """Stateful fake for lmc._get.

    Returns configured responses by path and records every call.
    """

    def __init__(self, responses: dict[str, tuple[int | None, bytes, str, int]]):
        self.responses = responses
        self.calls: list[tuple[str, str, str | None]] = []

    def __call__(
        self,
        base_url: str,
        path: str,
        *,
        api_key: str | None = None,
        timeout: float = lmc.DEFAULT_TIMEOUT,
    ) -> tuple[int | None, bytes, str, int]:
        self.calls.append((base_url, path, api_key))
        return self.responses.get(path, (200, b"{}", "application/json", 10))


def _json_response(payload: dict[str, Any]) -> tuple[int, bytes, str, int]:
    return (200, json.dumps(payload).encode("utf-8"), "application/json", 12)


def test_check_endpoint_ok_when_status_and_keys_present(monkeypatch):
    fake = _FakeGet({"/api/v1/mira/health": _json_response({"status": "healthy"})})
    monkeypatch.setattr(lmc, "_get", fake)
    probe = lmc._check_endpoint(
        "https://example.test", "/api/v1/mira/health", None, 5.0, ("status",)
    )
    assert probe.ok is True
    assert probe.status == 200
    assert probe.evidence == "ok keys=status"


def test_check_endpoint_fails_when_status_not_200(monkeypatch):
    fake = _FakeGet({"/api/v1/mira/system": (503, b"unavailable", "text/plain", 5)})
    monkeypatch.setattr(lmc, "_get", fake)
    probe = lmc._check_endpoint(
        "https://example.test", "/api/v1/mira/system", "key", 5.0, ("status",)
    )
    assert probe.ok is False
    assert probe.status == 503


def test_check_endpoint_fails_when_required_keys_missing(monkeypatch):
    fake = _FakeGet({"/api/v1/mira/metrics": _json_response({"status": "healthy"})})
    monkeypatch.setattr(lmc, "_get", fake)
    probe = lmc._check_endpoint(
        "https://example.test", "/api/v1/mira/metrics", "key", 5.0, ("status", "metrics")
    )
    assert probe.ok is False
    assert "missing keys: metrics" in probe.evidence


def test_check_endpoint_fails_when_body_status_is_error(monkeypatch):
    # Mira returns HTTP 200 + {"status": "error"} on a Firestore failure.
    # Key-presence alone would read that as healthy; the value must be checked.
    fake = _FakeGet({"/api/v1/mira/leads/summary": _json_response({"status": "error"})})
    monkeypatch.setattr(lmc, "_get", fake)
    probe = lmc._check_endpoint(
        "https://example.test", "/api/v1/mira/leads/summary", "key", 5.0, ("status",)
    )
    assert probe.ok is False
    assert "status='error'" in probe.evidence


def test_run_checks_without_api_key_probes_public_endpoints_only(monkeypatch):
    fake = _FakeGet(
        {
            "/healthz/": _json_response({"status": "ok", "version": "abc"}),
            "/api/v1/mira/health": _json_response({"status": "healthy"}),
        }
    )
    monkeypatch.setattr(lmc, "_get", fake)

    probes, warnings = lmc.run_checks("https://example.test", None, timeout=5.0)

    names = {p.name for p in probes}
    assert "/healthz/" in names
    assert "/api/v1/mira/health" in names
    assert "partner endpoints" in names
    assert any(p.name == "partner endpoints" and not p.ok for p in probes)
    # No partner paths were hit.
    partner_calls = [c for c in fake.calls if c[1].startswith("/api/v1/") and c[1] != "/api/v1/mira/health"]
    assert not partner_calls
    assert warnings == []


def test_run_checks_with_api_key_covers_all_partner_endpoints(monkeypatch):
    responses: dict[str, tuple[int | None, bytes, str, int]] = {
        "/healthz/": _json_response({"status": "ok"}),
        "/api/v1/mira/health": _json_response({"status": "healthy"}),
    }
    for path, keys in (*lmc.MIRA_ENDPOINTS, *lmc.STATUS_ENDPOINTS):
        if path == "/api/v1/mira/leads/summary":
            responses[path] = _json_response(
                {"status": "healthy", "total": 10, "by_status": {"new": 2}}
            )
        elif path in ("/api/v1/cutover/dns-status", "/api/v1/cutover/mx-status"):
            responses[path] = _json_response(
                {"domain": "example.test", "overall_ready": True, "apex": {}, "www": {}}
                if "dns" in path
                else {"domain": "example.test", "overall_ready": True, "inbound": {}, "resend": {}}
            )
        else:
            responses[path] = _json_response(
                {key: ("healthy" if key == "status" else "") for key in keys}
            )

    fake = _FakeGet(responses)
    monkeypatch.setattr(lmc, "_get", fake)

    probes, warnings = lmc.run_checks("https://example.test", "secret", timeout=5.0)

    names = {p.name for p in probes}
    expected = {path for path, _ in (*lmc.MIRA_ENDPOINTS, *lmc.STATUS_ENDPOINTS)}
    expected.remove("/api/v1/mira/leads/summary")
    expected.add("lead backlog")
    assert expected.issubset(names)
    assert all(p.ok for p in probes)
    assert warnings == []

    # Auth header was sent on every protected partner call.
    protected_calls = [
        c for c in fake.calls if c[1].startswith("/api/v1/") and c[1] not in lmc.PUBLIC_ENDPOINTS
    ]
    assert all(c[2] == "secret" for c in protected_calls)


def test_lead_backlog_warning_when_new_exceeds_threshold(monkeypatch):
    fake = _FakeGet(
        {
            "/healthz/": _json_response({"status": "ok"}),
            "/api/v1/mira/health": _json_response({"status": "healthy"}),
            "/api/v1/mira/leads/summary": _json_response(
                {"status": "healthy", "total": 70, "by_status": {"new": 63}}
            ),
        }
    )
    monkeypatch.setattr(lmc, "_get", fake)

    probes, warnings = lmc.run_checks(
        "https://example.test", "secret", timeout=5.0, lead_threshold=50
    )

    backlog_probe = next(p for p in probes if p.name == "lead backlog")
    assert backlog_probe.ok is True
    assert "63 leads in 'new' status exceed threshold (50)" in warnings


def test_cutover_not_ready_warning(monkeypatch):
    fake = _FakeGet(
        {
            "/healthz/": _json_response({"status": "ok"}),
            "/api/v1/mira/health": _json_response({"status": "healthy"}),
            "/api/v1/cutover/dns-status": _json_response(
                {
                    "domain": "example.test",
                    "overall_ready": False,
                    "apex": {},
                    "www": {},
                }
            ),
            "/api/v1/cutover/mx-status": _json_response(
                {
                    "domain": "example.test",
                    "overall_ready": True,
                    "inbound": {},
                    "resend": {},
                }
            ),
        }
    )
    monkeypatch.setattr(lmc, "_get", fake)

    probes, warnings = lmc.run_checks("https://example.test", "secret", timeout=5.0)

    dns_probe = next(p for p in probes if p.name == "/api/v1/cutover/dns-status")
    assert dns_probe.ok is True
    assert "DNS cutover is not yet overall_ready" in warnings
    assert "MX cutover is not yet overall_ready" not in warnings


def test_main_prints_json_and_exits_0_when_all_ok(monkeypatch, capsys):
    responses: dict[str, tuple[int | None, bytes, str, int]] = {
        "/healthz/": _json_response({"status": "ok"}),
        "/api/v1/mira/health": _json_response({"status": "healthy"}),
    }
    for path, keys in (*lmc.MIRA_ENDPOINTS, *lmc.STATUS_ENDPOINTS):
        if path == "/api/v1/mira/leads/summary":
            responses[path] = _json_response(
                {"status": "healthy", "total": 10, "by_status": {"new": 2}}
            )
        elif path in ("/api/v1/cutover/dns-status", "/api/v1/cutover/mx-status"):
            responses[path] = _json_response(
                {"domain": "example.test", "overall_ready": True, "apex": {}, "www": {}}
                if "dns" in path
                else {"domain": "example.test", "overall_ready": True, "inbound": {}, "resend": {}}
            )
        else:
            responses[path] = _json_response(
                {key: ("healthy" if key == "status" else "") for key in keys}
            )
    fake = _FakeGet(responses)
    monkeypatch.setattr(lmc, "_get", fake)

    rc = lmc.main(["--base-url", "https://example.test", "--api-key", "secret"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["summary"]["failed"] == 0
    assert rc == 0


def test_main_exits_1_when_any_probe_fails(monkeypatch, capsys):
    fake = _FakeGet(
        {
            "/healthz/": (503, b"bad", "text/plain", 5),
            "/api/v1/mira/health": _json_response({"status": "healthy"}),
        }
    )
    monkeypatch.setattr(lmc, "_get", fake)

    rc = lmc.main(["--base-url", "https://example.test"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["summary"]["failed"] >= 1
    assert rc == 1
