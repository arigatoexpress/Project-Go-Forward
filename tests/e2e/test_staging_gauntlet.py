"""E2E staging gauntlet tests — launch-blocker item 9 in LAUNCH_READINESS.md.

Complements tests/e2e/test_staging_smoke.py with the two flows that were
previously untestable because their prerequisites did not exist yet:

* Partner API *authorized* access (needs the `tho-api-key` secret created by
  the Ops bootstrap workflow, item 3).
* DocuSeal e-sign flow (needs the e-sign server deployed, item 6).

Everything skips gracefully when its env vars are unset, so the module is
green locally and in CI.  Operator cheatsheet once staging exists:

    export STAGING_URL="https://staging---<service>.run.app"
    export ADMIN_PIN="<prod-strength pin>"
    export THO_API_KEY="$(gcloud secrets versions access latest --secret=tho-api-key)"
    # after DocuSeal deploy (item 6):
    export DOCUSEAL_WEBHOOK_SECRET="<same value as the Cloud Run secret>"
    export ESIGN_E2E=1
    export ESIGN_TEMPLATE_NAME="<DocuSeal template name from the deploy runbook>"
    export ESIGN_SIGNER_EMAIL="<an inbox you control>"
    pytest tests/e2e/test_staging_gauntlet.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time

import httpx
import pytest

STAGING_URL = os.environ.get("STAGING_URL", "http://localhost:8080")
ADMIN_PIN = os.environ.get("ADMIN_PIN")
THO_API_KEY = os.environ.get("THO_API_KEY")
DOCUSEAL_WEBHOOK_SECRET = os.environ.get("DOCUSEAL_WEBHOOK_SECRET")
ESIGN_E2E = os.environ.get("ESIGN_E2E") == "1"
ESIGN_TEMPLATE_NAME = os.environ.get("ESIGN_TEMPLATE_NAME")
ESIGN_SIGNER_EMAIL = os.environ.get("ESIGN_SIGNER_EMAIL")

needs_staging = pytest.mark.skipif(
    not os.environ.get("STAGING_URL"),
    reason="no staging URL",
)

needs_admin_pin = pytest.mark.skipif(
    not ADMIN_PIN,
    reason="no admin PIN",
)

needs_partner_key = pytest.mark.skipif(
    not THO_API_KEY,
    reason="no THO_API_KEY (run the Ops bootstrap workflow first)",
)

needs_webhook_secret = pytest.mark.skipif(
    not DOCUSEAL_WEBHOOK_SECRET,
    reason="no DOCUSEAL_WEBHOOK_SECRET (DocuSeal not deployed yet)",
)

needs_esign = pytest.mark.skipif(
    not (ESIGN_E2E and ESIGN_TEMPLATE_NAME and ESIGN_SIGNER_EMAIL),
    reason="live e-sign send not armed (set ESIGN_E2E=1, ESIGN_TEMPLATE_NAME, ESIGN_SIGNER_EMAIL)",
)


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=STAGING_URL, timeout=30.0, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    """Obtain a valid admin token by verifying the synthetic PIN."""
    if not ADMIN_PIN:
        pytest.skip("no admin PIN")
    resp = client.post("/api/admin/verify", json={"pin": ADMIN_PIN})
    assert resp.status_code == 200, f"admin verify failed: {resp.text}"
    assert resp.json().get("success") is True
    token = ""
    for raw in resp.headers.get_list("set-cookie"):
        m = re.search(r"tho_admin_token=([^;]+)", raw)
        if m:
            token = m.group(1)
            break
    if not token:
        pytest.skip("could not extract admin token from set-cookie")
    return token


# ─── Partner API authorized access (gauntlet: "partner API auth") ────────────


@needs_staging
@needs_partner_key
def test_partner_api_inventory_authorized(client):
    """Gauntlet: a valid partner key unlocks /api/v1/inventory (item 3 clears 503)."""
    r = client.get("/api/v1/inventory", headers={"X-API-Key": THO_API_KEY})
    assert r.status_code == 200, f"authorized partner call failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    homes = body if isinstance(body, list) else body.get("items") or body.get("homes") or []
    assert isinstance(homes, list), f"unexpected inventory payload shape: {type(body)}"
    # Non-emptiness is owned by test_staging_smoke.py::test_public_inventory_shape;
    # this test owns "a valid key gets a well-shaped 200", which must also hold
    # against a fresh local instance with no inventory data.


@needs_staging
@needs_partner_key
def test_partner_api_bearer_auth_variant(client):
    """Gauntlet: Authorization: Bearer works as well as X-API-Key."""
    r = client.get("/api/v1/customers", headers={"Authorization": f"Bearer {THO_API_KEY}"})
    assert r.status_code == 200, f"bearer partner call failed: {r.status_code} {r.text[:200]}"


@needs_staging
def test_partner_api_rejects_wrong_key(client):
    """Gauntlet: a wrong key is rejected once keys are configured (never 200)."""
    r = client.get("/api/v1/inventory", headers={"X-API-Key": "tho-e2e-definitely-wrong-key"})
    if r.status_code == 503:
        pytest.skip("no partner keys configured on target yet (fail-closed 503)")
    assert r.status_code == 401, f"wrong key must be 401, got {r.status_code}"
    assert r.json().get("detail"), "rejection should carry a detail message"


# ─── Customer secure-hub portal (keyless, phone-gated) ───────────────────────


@needs_staging
def test_customer_deal_portal_not_an_existence_oracle(client):
    """Gauntlet: unknown deal and wrong phone both return the same 403, never 5xx."""
    deal_id = f"e2e-nonexistent-{int(time.time())}"
    r = client.get(f"/api/v1/customer/deal/{deal_id}")
    assert r.status_code == 403, f"unknown deal must be 403 (anti-oracle), got {r.status_code}"
    assert r.json().get("detail"), "403 should carry the generic verification message"


# ─── DocuSeal e-sign flow (gauntlet: "e-sign flow", launch item 6) ───────────


@needs_staging
def test_docuseal_webhook_endpoint_liveness(client):
    """Gauntlet: webhook route is deployed and never 5xx on a bogus signature.

    Pre-deploy (no DOCUSEAL_WEBHOOK_SECRET server-side) the handler is a 200
    no-op; post-deploy a bad signature must be a 401.
    """
    r = client.post(
        "/api/docuseal/webhook",
        content=b'{"event_type": "form.completed", "data": {}}',
        headers={"Content-Type": "application/json", "X-Docuseal-Signature": "bogus"},
    )
    assert r.status_code in (200, 401), (
        f"webhook must be a 200 no-op or a 401 rejection, got {r.status_code}"
    )
    if r.status_code == 200:
        body = r.json()
        assert body.get("status") == "ignored", f"unexpected webhook body: {body}"


@needs_staging
@needs_webhook_secret
def test_docuseal_webhook_valid_hmac_roundtrip(client):
    """Gauntlet: a correctly-signed webhook passes HMAC verification.

    Sends a form.completed event with no deal_id/documents so the handler
    verifies the signature and then ignores the payload — no Firestore/GCS
    writes. Fails loudly if the operator's secret does not match the server.
    """
    payload = json.dumps({"event_type": "form.completed", "data": {"id": 0}}).encode()
    sig = hmac.new(DOCUSEAL_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/docuseal/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "X-Docuseal-Signature": sig},
    )
    assert r.status_code == 200, (
        f"valid HMAC was rejected ({r.status_code}) — DOCUSEAL_WEBHOOK_SECRET mismatch?"
    )
    body = r.json()
    assert body.get("status") == "ignored", f"expected ignored, got: {body}"


@needs_staging
@needs_admin_pin
@needs_esign
def test_esign_send_flow(client, admin_token):
    """Gauntlet: end-to-end DocuSeal submission from the admin surface.

    Armed only with ESIGN_E2E=1 — opting in means DocuSeal is expected to be
    live, so a 501 'not configured' is a FAILURE, not a skip.
    """
    deal_id = f"e2e-gauntlet-{int(time.time())}"
    r = client.post(
        "/api/docuseal/send",
        headers={"X-Admin-Token": admin_token},
        json={
            "deal_id": deal_id,
            "template_name": ESIGN_TEMPLATE_NAME,
            "signer_email": ESIGN_SIGNER_EMAIL,
            "signer_name": "E2E Gauntlet Signer",
        },
    )
    assert r.status_code != 501, (
        "DocuSeal not configured on target but ESIGN_E2E=1 — finish the "
        "deploy runbook steps (docs/DOCUSEAL_DEPLOY_RUNBOOK.md) first"
    )
    assert r.status_code == 200, f"e-sign send failed: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert body.get("success") is True, f"e-sign send unsuccessful: {body}"
    assert body.get("submission_id") or body.get("signing_url"), (
        f"e-sign response lacks submission handle: {body}"
    )
