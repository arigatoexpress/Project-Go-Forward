"""Readiness probe (/readyz) — a REAL deploy-surface gate, not liveness.

`/healthz` answers "is the process up?" (liveness). It returns 200 even when the
deployed image is missing runtime assets — which is exactly how the 2026-06-23
chat outage (#223) went unnoticed: the container booted fine, `/healthz` stayed
green, but `prompts/*.md` had been stripped by a blanket `*.md` ignore rule, so
the ADK runner could not init and every `/run` errored.

`/readyz` exercises the runtime dependencies a working request actually needs:

  (a) the agent prompt templates render (same prompt_loader the ADK runner uses),
  (b) the regulatory document templates (tho_documents/*.pdf) are present, and
  (c) Firestore reachability is reported best-effort (never hard-fails locally).

It returns 200 {"ready": true, "checks": {...}} when all hard checks pass, or 503
{"ready": false, ...} naming the failing check. This is the probe that WOULD have
caught #223: strip the prompts and /readyz must go 503 while /healthz stays 200.
"""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

client = TestClient(main.app)


def test_readyz_returns_200_and_ready_true_when_assets_present():
    resp = client.get("/readyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    checks = body["checks"]
    # The three runtime dimensions a working request depends on are all reported.
    assert checks["prompts"]["ok"] is True
    assert checks["documents"]["ok"] is True
    assert "firestore" in checks  # best-effort, reported either way


def test_readyz_is_distinct_from_healthz_liveness():
    """/healthz answers liveness; /readyz answers readiness — different probes."""
    health = client.get("/healthz/")
    assert health.status_code == 200
    assert "ready" not in health.json()  # liveness probe does NOT do readiness work

    ready = client.get("/readyz")
    assert "ready" in ready.json()


def test_readyz_503_when_prompt_templates_unloadable(monkeypatch):
    """The #223 incident class: prompts stripped from the image -> probe must fail.

    We simulate the stripped-prompt image by making the SAME prompt loader the
    ADK runner uses raise, then assert /readyz reports 503 and names the prompts
    check as the failure. A liveness-only probe would stay green here.
    """

    def _boom(*_a, **_k):
        raise FileNotFoundError("prompts/sales_agent.md missing from image")

    monkeypatch.setattr(main, "render_prompt", _boom)

    resp = client.get("/readyz")
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["ready"] is False
    assert body["failed"] == "prompts" or body["checks"]["prompts"]["ok"] is False


def test_readyz_503_when_regulatory_documents_missing(monkeypatch):
    """A deploy that strips tho_documents/ (regulatory PDFs) must fail readiness.

    Point the document-readiness check at an empty directory so the expected
    regulatory templates are absent, and assert the probe goes 503 naming the
    documents check.
    """
    monkeypatch.setattr(main, "_readyz_documents_dir", lambda: "/nonexistent/tho_documents")

    resp = client.get("/readyz")
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["ready"] is False
    assert body["failed"] == "documents" or body["checks"]["documents"]["ok"] is False


def test_readyz_firestore_failure_is_soft(monkeypatch):
    """Firestore unreachability is reported but does NOT fail the probe locally.

    Cloud Run readiness should not flap on a transient DB blip — the hard gates
    are the image-asset checks. Firestore is best-effort: ok may be False, but
    the overall probe still returns 200 as long as prompts + documents are fine.
    """

    def _fs_boom():
        raise RuntimeError("firestore unreachable")

    monkeypatch.setattr(main, "_readyz_check_firestore", _fs_boom)

    resp = client.get("/readyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"]["firestore"]["ok"] is False


def _req(path: str, host: str = "candidate---x.a.run.app", method: str = "GET"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"host", host.encode())],
        "query_string": b"",
        "scheme": "https",
        "server": (host, 443),
    }
    return Request(scope)


def test_readyz_exempt_from_canonical_host_redirect():
    """A probe must be hittable directly on the *.run.app URL (like /healthz),
    not 308-redirected to the canonical vanity host — otherwise CI cannot probe
    the candidate revision pre-cutover."""
    assert main._should_redirect_to_canonical_host(_req("/readyz")) is False
    assert main._should_redirect_to_canonical_host(_req("/readyz/")) is False
    # Control: a normal customer page on the run.app host IS still redirected.
    assert main._should_redirect_to_canonical_host(_req("/inventory")) is True


def test_readyz_is_rate_limit_exempt():
    assert main._is_rate_limit_exempt_path("/readyz") is True
    assert main._is_rate_limit_exempt_path("/readyz/") is True
