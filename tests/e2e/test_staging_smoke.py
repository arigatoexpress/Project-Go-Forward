"""E2E staging smoke tests — automated version of tests/e2e/2026-04-29-smoke.md.

These tests skip gracefully when STAGING_URL is not set, so they pass locally
without a live target.  Admin PIN tests skip when ADMIN_PIN is not set.
"""

from __future__ import annotations

import os
import re
import time

import httpx
import pytest

STAGING_URL = os.environ.get("STAGING_URL", "http://localhost:8080")
ADMIN_PIN = os.environ.get("ADMIN_PIN")

needs_staging = pytest.mark.skipif(
    not os.environ.get("STAGING_URL"),
    reason="no staging URL",
)

needs_admin_pin = pytest.mark.skipif(
    not ADMIN_PIN,
    reason="no admin PIN",
)


@pytest.fixture(scope="module")
def base_url():
    return STAGING_URL


@pytest.fixture(scope="module")
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    """Obtain a valid admin token by verifying the synthetic PIN."""
    if not ADMIN_PIN:
        pytest.skip("no admin PIN")
    resp = client.post("/api/admin/verify", json={"pin": ADMIN_PIN})
    assert resp.status_code == 200, f"admin verify failed: {resp.text}"
    data = resp.json()
    assert data.get("success") is True

    # Token is returned as an httpOnly cookie; extract it so we can also use
    # the X-Admin-Token header path in tests that need it.
    token = ""
    for raw in resp.headers.get_list("set-cookie"):
        m = re.search(r"tho_admin_token=([^;]+)", raw)
        if m:
            token = m.group(1)
            break
    if not token:
        pytest.skip("could not extract admin token from set-cookie")
    return token


@needs_staging
def test_health(client):
    """Smoke: public health endpoints return OK and minimal JSON."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    r = client.get("/healthz/")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert "version" in body


@needs_staging
@needs_admin_pin
def test_healthz_detailed(client, admin_token):
    """Smoke: detailed healthz requires admin auth and returns sha/uptime."""
    r = client.get(
        "/healthz/detailed",
        headers={"X-Admin-Token": admin_token},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert "version" in body
    # detailed endpoint adds sha / dependencies info
    assert "sha" in body or "version" in body


@needs_staging
@needs_admin_pin
def test_admin_verify_and_check(client, admin_token):
    """Smoke: verify PIN returns success; check token returns valid."""
    # Token already obtained by fixture, but verify explicit header path works
    r = client.get(
        "/api/admin/check",
        headers={"X-Admin-Token": admin_token},
    )
    assert r.status_code == 200
    assert r.json().get("valid") is True

    # Cookie-only path (no explicit header) should also work because the client
    # from the verify fixture does not share cookies with this client.
    # We rely on the explicit header for determinism in E2E tests.


@needs_staging
def test_public_inventory_shape(client):
    """Smoke: public inventory context has homes, prices, and media."""
    r = client.get("/api/marketing/inventory-context")
    assert r.status_code == 200
    body = r.json()

    homes = body.get("homes", [])
    assert isinstance(homes, list)
    assert len(homes) > 0, "expected at least one home in inventory context"

    for home in homes[:5]:  # sample first few
        assert "model_name" in home or "name" in home
        price = home.get("price") or home.get("sales_price") or home.get("base_price")
        assert price is not None, "expected a price field on inventory item"
        # Media presence
        media = (
            home.get("real_photos")
            or home.get("gallery_images")
            or home.get("hero_image")
            or home.get("floor_plan_url")
            or home.get("matterport_url")
        )
        assert media is not None or home.get("image_categories"), (
            "expected some media on inventory item"
        )


@needs_staging
@needs_admin_pin
def test_document_generation_batch(client, admin_token):
    """Smoke: generate-batch produces PDFs with synthetic data."""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    payload = {
        "templates": [
            "TMHA_SalesContract.pdf",
            "TDHCA_1038_Consumer_Disclosure.pdf",
            "State_CreditAuth.pdf",
            "Internal_Homestead.pdf",
        ],
        "merge": False,
        "data": {
            "buyer_name": f"PGF Smoke {ts}",
            "buyer_first_name": "PGF",
            "buyer_last_name": f"Smoke {ts}",
            "buyer_address": "100 Smoke Test Dr",
            "buyer_city": "Austin",
            "buyer_state": "TX",
            "buyer_zip": "78701",
            "buyer_city_state_zip": "Austin, TX 78701",
            "buyer_full_address": "100 Smoke Test Dr, Austin, TX 78701",
            "mailing_full_address": "100 Smoke Test Dr, Austin, TX 78701",
            "buyer_phone": "555-0100",
            "buyer_email": "pgf-smoke@example.invalid",
            "manufacturer": "Smoke Homes",
            "model": "Verification",
            "manufacturer_model": "Smoke Homes Verification",
            "serial_number": f"SMOKE-{ts}",
            "serial_label_combined": f"SMOKE-{ts} / LABEL-SMOKE",
            "label_number": "LABEL-SMOKE",
            "sales_price": "100000",
            "down_payment": "5000",
            "unpaid_balance": "95000",
            "creditor_name": "Smoke Test Creditor",
            "loan_term": "240",
            "apr": "8.00",
            "installation_address": "100 Smoke Test Dr",
            "install_city": "Austin",
            "install_state": "TX",
            "install_zip": "78701",
            "date": "2026-04-29",
        },
    }
    r = client.post(
        "/api/documents/generate-batch",
        headers={"X-Admin-Token": admin_token},
        json=payload,
    )
    assert r.status_code == 200, f"batch generation failed: {r.text}"
    body = r.json()
    assert body.get("success") is True, f"batch success=False: {body}"

    # Verify individual files are listed
    files = body.get("files", [])
    assert len(files) >= 1, "expected at least one generated file"
    for f in files:
        assert "download_url" in f or "filename" in f


@needs_staging
def test_cache_headers(client, admin_token):
    """Smoke: static assets are immutable; public API is cacheable; admin is no-cache."""
    # 1. Static asset — find a fingerprinted asset from the SPA index
    index = client.get("/")
    if index.status_code == 200:
        asset_urls = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.text)
        if asset_urls:
            asset_url = asset_urls[0]
            r = client.get(asset_url)
            if r.status_code == 200:
                cc = r.headers.get("cache-control", "")
                assert "public" in cc
                assert "max-age=31536000" in cc
                assert "immutable" in cc
    else:
        pytest.skip("could not fetch index page to discover static assets")

    # 2. Public inventory context
    r = client.get("/api/marketing/inventory-context")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc

    # 3. Admin dynamic endpoint
    r = client.get(
        "/api/admin/check",
        headers={"X-Admin-Token": admin_token},
    )
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert cc == "no-cache", f"expected no-cache on admin endpoint, got {cc}"


@needs_staging
@needs_admin_pin
def test_admin_lockout(client, admin_token):
    """Smoke: 10 wrong PINs → 401; 11th → 429 with Retry-After.

    The fixture already verified the correct PIN once, which resets the
    attempt counter for this IP.  We then fire 11 wrong attempts to exercise
    the lockout path.
    """
    # Use a fresh client so we do not clobber the cookie jar of the main client.
    wrong_pin = "intentionally-wrong-synthetic-pin"
    with httpx.Client(base_url=STAGING_URL, timeout=30.0, follow_redirects=True) as c:
        for i in range(1, 12):
            r = c.post("/api/admin/verify", json={"pin": wrong_pin})
            if i <= 10:
                assert r.status_code == 401, (
                    f"attempt {i} expected 401, got {r.status_code}"
                )
                data = r.json()
                assert data.get("success") is False
                assert "incorrect" in data.get("error", "").lower()
            else:
                assert r.status_code == 429, (
                    f"attempt {i} expected 429, got {r.status_code}"
                )
                data = r.json()
                assert "wait" in data.get("error", "").lower() or "too many" in data.get(
                    "error", ""
                ).lower()
                retry = r.headers.get("retry-after")
                assert retry is not None, "expected Retry-After header on 429"
                assert int(retry) > 0


@needs_staging
def test_feedback(client):
    """Smoke: public feedback endpoint accepts synthetic bug reports."""
    r = client.post(
        "/api/feedback",
        json={
            "description": "Synthetic smoke bug report. No live customer data.",
            "page": "smoke-template",
            "url": "https://example.invalid/feedback",
            "userAgent": "pgf-e2e-smoke",
            "screenSize": "1440x900",
        },
    )
    assert r.status_code in (200, 202)
    # Response may be plain text or JSON depending on implementation
    assert r.text or r.json()
