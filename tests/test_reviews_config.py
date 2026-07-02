"""Tests for the review-request helper config endpoint.

GET /api/admin/reviews/config exposes whether the Google-review request
helper is enabled (GOOGLE_REVIEW_LINK env var set) and the link itself.
The feature is inert until an operator sets the env var — no link, no UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import load_app  # noqa: E402


def _make_admin_client(monkeypatch):
    main, _fake_db, _fake_logger = load_app(monkeypatch, tho_api_key="tho-secret")
    client = TestClient(main.app)
    token = main._create_admin_token()
    return client, main, {"Authorization": f"Bearer {token}"}


def test_reviews_config_requires_admin(monkeypatch):
    client, _main, _headers = _make_admin_client(monkeypatch)

    response = client.get("/api/admin/reviews/config")

    assert response.status_code == 401


def test_reviews_config_disabled_when_link_unset(monkeypatch):
    monkeypatch.delenv("GOOGLE_REVIEW_LINK", raising=False)
    monkeypatch.delenv("FF_GOOGLE_REVIEW_LINK", raising=False)
    monkeypatch.delenv("FF_GOOGLE_REVIEW_LINK_VALUE", raising=False)
    client, _main, headers = _make_admin_client(monkeypatch)

    response = client.get("/api/admin/reviews/config", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["enabled"] is False
    assert body["review_link"] is None


def test_reviews_config_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_REVIEW_LINK", "https://g.page/r/XXXX/review")
    client, _main, headers = _make_admin_client(monkeypatch)

    response = client.get("/api/admin/reviews/config", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["enabled"] is True
    assert body["review_link"] == "https://g.page/r/XXXX/review"


def test_reviews_config_whitespace_link_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GOOGLE_REVIEW_LINK", "   ")
    monkeypatch.delenv("FF_GOOGLE_REVIEW_LINK", raising=False)
    monkeypatch.delenv("FF_GOOGLE_REVIEW_LINK_VALUE", raising=False)
    client, _main, headers = _make_admin_client(monkeypatch)

    response = client.get("/api/admin/reviews/config", headers=headers)

    body = response.json()
    assert body["enabled"] is False
    assert body["review_link"] is None


def test_reviews_config_feature_flag_fallback(monkeypatch):
    """The centralized feature-flag system (FF_*_VALUE) also works as a source."""
    monkeypatch.delenv("GOOGLE_REVIEW_LINK", raising=False)
    monkeypatch.setenv("FF_GOOGLE_REVIEW_LINK_VALUE", "https://g.page/r/YYYY/review")
    client, _main, headers = _make_admin_client(monkeypatch)

    response = client.get("/api/admin/reviews/config", headers=headers)

    body = response.json()
    assert body["enabled"] is True
    assert body["review_link"] == "https://g.page/r/YYYY/review"
