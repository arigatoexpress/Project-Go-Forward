"""Tests for the public /review QR-code redirect.

GET /review is a public (no-auth) 302 redirect to the configured Google
review link (env GOOGLE_REVIEW_LINK, falling back to the feature-flag /
config.yaml value — same resolution as /api/admin/reviews/config). The QR
codes on the lot / in the office point at our domain so the destination
stays trackable and re-pointable.

Security invariants:
- The redirect target comes ONLY from config — request params can never
  change it (no open redirect).
- With no link configured the route fails closed with 404.
- ?src= is tracking-only: validated (alnum + dash, max 32 chars) and
  recorded as a best-effort analytics event; invalid values are ignored.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402

LINK = "https://search.google.com/local/writereview?placeid=TEST123"


def _clear_config_fallback(monkeypatch):
    """Neutralize the config.yaml feature_flags fallback.

    config.yaml ships a real GOOGLE_REVIEW_LINK, so "unset" scenarios must
    silence the config source to exercise pure env-var behavior.
    """
    from tools import feature_flags

    monkeypatch.setattr(feature_flags, "_read_config", lambda name: None)


def _make_client(monkeypatch, link: str | None = LINK):
    if link is None:
        monkeypatch.delenv("GOOGLE_REVIEW_LINK", raising=False)
        monkeypatch.delenv("FF_GOOGLE_REVIEW_LINK", raising=False)
        monkeypatch.delenv("FF_GOOGLE_REVIEW_LINK_VALUE", raising=False)
        _clear_config_fallback(monkeypatch)
    else:
        monkeypatch.setenv("GOOGLE_REVIEW_LINK", link)
    client, main, *_ = create_client(monkeypatch)
    return client, main


def _capture_analytics(monkeypatch, main):
    captured: dict[str, list] = {}

    class RecColl:
        def __init__(self, name):
            self.name = name

        def add(self, doc):
            captured.setdefault(self.name, []).append(doc)

    monkeypatch.setattr(main._db.db, "collection", lambda name: RecColl(name))
    return captured


def test_review_redirects_to_configured_link(monkeypatch):
    client, _main = _make_client(monkeypatch)

    r = client.get("/review", follow_redirects=False)

    assert r.status_code == 302
    assert r.headers["location"] == LINK


def test_review_is_public_no_auth_required(monkeypatch):
    client, _main = _make_client(monkeypatch)

    r = client.get("/review", follow_redirects=False)

    assert r.status_code == 302  # no 401 — public route


def test_review_404_when_link_unconfigured(monkeypatch):
    client, _main = _make_client(monkeypatch, link=None)

    r = client.get("/review", follow_redirects=False)

    assert r.status_code == 404


def test_review_falls_back_to_feature_flag_value(monkeypatch):
    monkeypatch.delenv("GOOGLE_REVIEW_LINK", raising=False)
    monkeypatch.setenv("FF_GOOGLE_REVIEW_LINK_VALUE", "https://g.page/r/FLAG/review")
    client, _main, *_ = create_client(monkeypatch)

    r = client.get("/review", follow_redirects=False)

    assert r.status_code == 302
    assert r.headers["location"] == "https://g.page/r/FLAG/review"


def test_review_valid_src_recorded_in_analytics(monkeypatch):
    client, main = _make_client(monkeypatch)
    captured = _capture_analytics(monkeypatch, main)

    r = client.get("/review?src=qr-lot", follow_redirects=False)

    assert r.status_code == 302
    events = captured.get("analytics_events", [])
    assert len(events) == 1
    rec = events[0]
    assert rec["event"] == "review_redirect"
    assert rec["props"]["src"] == "qr-lot"
    assert "created_at" in rec


def test_review_invalid_src_ignored_but_still_redirects(monkeypatch):
    client, main = _make_client(monkeypatch)
    captured = _capture_analytics(monkeypatch, main)

    for bad in (
        "qr lot",  # space
        "a" * 33,  # too long
        "qr_lot!",  # punctuation
        "%2F..%2Fetc",  # traversal-ish
        "<script>",  # markup
    ):
        r = client.get(f"/review?src={bad}", follow_redirects=False)
        assert r.status_code == 302, bad
        assert r.headers["location"] == LINK, bad

    for rec in captured.get("analytics_events", []):
        assert rec["props"]["src"] == "direct"


def test_review_params_can_never_change_target(monkeypatch):
    """No open redirect: the target comes ONLY from config."""
    client, _main = _make_client(monkeypatch)

    for path in (
        "/review?src=qr-lot",
        "/review?url=https://evil.example.com",
        "/review?redirect=https://evil.example.com",
        "/review?src=qr-lot&next=//evil.example.com",
        "/review?src=" + "x" * 500,
    ):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302, path
        assert r.headers["location"] == LINK, path
        assert "evil" not in r.headers["location"], path


def test_review_analytics_failure_does_not_break_redirect(monkeypatch):
    client, main = _make_client(monkeypatch)

    def boom(_name):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(main._db.db, "collection", boom)

    r = client.get("/review?src=qr-office", follow_redirects=False)

    assert r.status_code == 302
    assert r.headers["location"] == LINK
