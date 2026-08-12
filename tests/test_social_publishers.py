"""Tests for the fail-closed social publishing adapters.

These assert the safety contract of ``tools/social_publishers.py``:
nothing is published unless the explicit ``THO_SOCIAL_PUBLISH_ENABLED`` gate is
on and the required tokens are configured, readiness reporting is accurate, and
the opt-in UTM CTA link builder stays a strict no-op by default.

All environment access is monkeypatched and the module-level ``requests`` is
replaced with a guard that fails loudly if any test would make a real HTTP call.
"""

from __future__ import annotations

import pytest

from tools import social_publishers

# Every env var the module reads. We clear all of them per test so the suite is
# hermetic regardless of the developer's shell or CI secrets.
_SOCIAL_ENV_VARS = (
    "THO_SOCIAL_PUBLISH_ENABLED",
    "THO_UTM_CTA_ENABLED",
    "THO_UTM_SOURCE",
    "THO_UTM_MEDIUM",
    "PUBLIC_SITE_URL",
    "TIKTOK_ACCESS_TOKEN",
    "TIKTOK_PRIVACY_LEVEL",
    "META_ACCESS_TOKEN",
    "META_GRAPH_VERSION",
    "INSTAGRAM_BUSINESS_ACCOUNT_ID",
)


class _NoHTTP:
    """Stand-in for ``requests`` that fails if any HTTP method is invoked."""

    def __getattr__(self, name: str):
        def _boom(*args, **kwargs):  # pragma: no cover - only fires on misuse
            raise AssertionError(
                f"Unexpected real HTTP call: requests.{name}({args!r}, {kwargs!r})"
            )

        return _boom


@pytest.fixture(autouse=True)
def _isolate_env_and_block_http(monkeypatch):
    """Clear all social env vars and forbid real HTTP for every test."""
    for var in _SOCIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # The module imports ``requests`` at module scope, so patch it there.
    monkeypatch.setattr(social_publishers, "requests", _NoHTTP())
    # _canonical_origin() can fall back to config_loader.get_business(); keep the
    # CTA tests deterministic by relying only on PUBLIC_SITE_URL (set per test).
    return monkeypatch


# ---------------------------------------------------------------------------
# (0) Instagram Reel async-ingestion poll: a media container is processed
#     asynchronously, so publishing before it reaches FINISHED fails. The
#     adapter must poll status_code and fail-closed (never publish) on ERROR
#     or timeout.
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeRequests:
    """Scripted stand-in for requests: media POST -> container id, GET -> next
    status from the sequence, media_publish POST -> post id. Records all calls."""

    def __init__(self, status_sequence):
        self._status = list(status_sequence)
        self.calls = []

    def post(self, url, data=None, timeout=None, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/media"):
            return _FakeResp({"id": "C1"})
        if url.endswith("/media_publish"):
            return _FakeResp({"id": "P1"})
        return _FakeResp({})

    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append(("GET", url))
        code = self._status.pop(0) if self._status else "FINISHED"
        return _FakeResp({"status_code": code})


def _ig_env(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-tok")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "ig-123")


def test_reel_polls_until_finished_then_publishes(monkeypatch):
    _ig_env(monkeypatch)
    fake = _FakeRequests(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    monkeypatch.setattr(social_publishers, "requests", fake)
    monkeypatch.setattr(social_publishers.time, "sleep", lambda s: None)

    result = social_publishers._publish_instagram_reel("https://cdn.example.com/r.mp4", "cap")

    assert result["success"] is True
    assert result["creation_id"] == "C1"
    gets = [i for i, (m, u) in enumerate(fake.calls) if m == "GET"]
    assert len(gets) == 3  # polled until FINISHED
    pub = next(i for i, (m, u) in enumerate(fake.calls) if u.endswith("/media_publish"))
    assert pub > gets[-1]  # published only AFTER the FINISHED status


def test_reel_fails_closed_on_error_status(monkeypatch):
    _ig_env(monkeypatch)
    fake = _FakeRequests(["IN_PROGRESS", "ERROR"])
    monkeypatch.setattr(social_publishers, "requests", fake)
    monkeypatch.setattr(social_publishers.time, "sleep", lambda s: None)

    result = social_publishers._publish_instagram_reel("https://cdn.example.com/r.mp4", "cap")

    assert result["success"] is False
    assert not any(u.endswith("/media_publish") for m, u in fake.calls)  # never published


def test_reel_fails_closed_on_timeout(monkeypatch):
    _ig_env(monkeypatch)
    monkeypatch.setenv("META_REEL_POLL_ATTEMPTS", "3")
    fake = _FakeRequests(["IN_PROGRESS"] * 10)  # never finishes
    monkeypatch.setattr(social_publishers, "requests", fake)
    monkeypatch.setattr(social_publishers.time, "sleep", lambda s: None)

    result = social_publishers._publish_instagram_reel("https://cdn.example.com/r.mp4", "cap")

    assert result["success"] is False
    assert not any(u.endswith("/media_publish") for m, u in fake.calls)
    assert sum(1 for m, u in fake.calls if m == "GET") == 3  # respected the bound


def test_instagram_poll_waits_for_finished_before_publish(monkeypatch):
    _ig_env(monkeypatch)
    fake = _FakeRequests(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    monkeypatch.setattr(social_publishers, "requests", fake)
    monkeypatch.setattr(social_publishers.time, "sleep", lambda *_: None)

    result = social_publishers._publish_instagram_reel("https://cdn.example.com/r.mp4", "cap")

    assert result["success"] is True
    assert result.get("post_id") is not None
    gets = [i for i, (m, u) in enumerate(fake.calls) if m == "GET"]
    assert len(gets) == 3  # polled until FINISHED
    pub = next(i for i, (m, u) in enumerate(fake.calls) if u.endswith("/media_publish"))
    assert pub > gets[-1]  # published only AFTER the FINISHED status


def test_instagram_poll_times_out_returns_failure(monkeypatch):
    _ig_env(monkeypatch)
    monkeypatch.setenv("META_REEL_POLL_ATTEMPTS", "3")
    fake = _FakeRequests(["IN_PROGRESS"] * 10)  # never finishes
    monkeypatch.setattr(social_publishers, "requests", fake)
    monkeypatch.setattr(social_publishers.time, "sleep", lambda *_: None)

    result = social_publishers._publish_instagram_reel("https://cdn.example.com/r.mp4", "cap")

    assert result["success"] is False
    assert not any(u.endswith("/media_publish") for m, u in fake.calls)
    assert sum(1 for m, u in fake.calls if m == "GET") == 3  # respected the bound


def test_instagram_poll_errored_status_returns_failure(monkeypatch):
    _ig_env(monkeypatch)
    fake = _FakeRequests(["IN_PROGRESS", "ERROR"])
    monkeypatch.setattr(social_publishers, "requests", fake)
    monkeypatch.setattr(social_publishers.time, "sleep", lambda *_: None)

    result = social_publishers._publish_instagram_reel("https://cdn.example.com/r.mp4", "cap")

    assert result["success"] is False
    assert not any(u.endswith("/media_publish") for m, u in fake.calls)  # never published


# ---------------------------------------------------------------------------
# (1) prepare_or_publish_social_post returns a draft (no publish) when the
#     THO_SOCIAL_PUBLISH_ENABLED gate is unset.
# ---------------------------------------------------------------------------


def test_prepare_returns_draft_when_publish_gate_unset(monkeypatch):
    # Fully configure tiktok tokens + site URL so the ONLY thing missing is the
    # publish gate. This isolates the gate as the reason it stays a draft.
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok-abc")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")
    # THO_SOCIAL_PUBLISH_ENABLED intentionally left unset.

    result = social_publishers.prepare_or_publish_social_post(
        platform="tiktok",
        content_type="video",
        scheduled_time="2026-07-01T12:00:00",
        caption="New listing tour",
        hashtags=["#texashomes"],
        video_url="https://cdn.example.com/clip.mp4",
    )

    assert result["success"] is True
    assert result["status"] == "draft_ready"
    assert result["live_integration"] is False
    assert result["publish_attempted"] is False
    assert result["post_id"].startswith("DRAFT-")
    # Draft preparation must be separated from any explicit publish action.
    assert "publish action" in result["publish_blocked_reason"].lower()
    # readiness embedded in the draft confirms publish is not enabled.
    assert result["social_readiness"]["publish_enabled"] is False


def test_prepare_draft_does_not_call_requests(monkeypatch):
    # Even with tokens present, an unset gate must not reach the network. The
    # autouse _NoHTTP guard would raise AssertionError if it did.
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-tok")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "ig-123")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")

    result = social_publishers.prepare_or_publish_social_post(
        platform="instagram_reels",
        content_type="video",
        scheduled_time="2026-07-01T12:00:00",
        caption="Reel",
        video_url="https://cdn.example.com/reel.mp4",
    )

    assert result["status"] == "draft_ready"
    assert result["publish_attempted"] is False


def test_prepare_publishes_only_with_explicit_allow_publish(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok-abc")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")
    monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
    calls = []
    monkeypatch.setattr(
        social_publishers,
        "_publish_tiktok_video",
        lambda *args: calls.append(args) or {"success": True, "post_id": "post-123"},
    )

    result = social_publishers.prepare_or_publish_social_post(
        platform="tiktok",
        content_type="video",
        scheduled_time="2026-07-01T12:00:00",
        caption="New listing tour",
        video_url="https://cdn.example.com/clip.mp4",
        allow_publish=True,
    )

    assert result["status"] == "published"
    assert result["publish_attempted"] is True
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# (2) social_readiness() reports instagram_reels configured=false with the
#     correct required_env when tokens are absent, true when present.
# ---------------------------------------------------------------------------


def test_social_readiness_instagram_unconfigured_lists_required_env(monkeypatch):
    # No META/IG/site tokens set (cleared by autouse fixture).
    readiness = social_publishers.social_readiness()
    ig = readiness["platforms"]["instagram_reels"]

    assert ig["configured"] is False
    # All three inputs are missing, so all three must be reported.
    assert set(ig["required_env"]) == {
        "META_ACCESS_TOKEN",
        "INSTAGRAM_BUSINESS_ACCOUNT_ID",
        "PUBLIC_SITE_URL",
    }
    assert ig["api"] == "Meta Instagram Content Publishing API"
    assert readiness["publish_enabled"] is False


def test_social_readiness_instagram_partial_lists_only_missing(monkeypatch):
    # Token present, account id + site URL still missing.
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-tok")

    ig = social_publishers.social_readiness()["platforms"]["instagram_reels"]

    assert ig["configured"] is False
    assert set(ig["required_env"]) == {
        "INSTAGRAM_BUSINESS_ACCOUNT_ID",
        "PUBLIC_SITE_URL",
    }


def test_social_readiness_instagram_configured_when_all_present(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-tok")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "ig-123")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")

    ig = social_publishers.social_readiness()["platforms"]["instagram_reels"]

    assert ig["configured"] is True
    assert ig["required_env"] == []


# ---------------------------------------------------------------------------
# (3) The UTM CTA link builder returns None when THO_UTM_CTA_ENABLED is unset,
#     and a correctly-tagged URL when enabled + a canonical origin is present.
# ---------------------------------------------------------------------------


def test_utm_cta_link_none_when_gate_unset(monkeypatch):
    # Origin present but the opt-in gate is off -> strict no-op.
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")

    assert social_publishers._utm_cta_link("tiktok", "Spring Sale") is None


def test_utm_cta_link_none_when_enabled_but_no_origin(monkeypatch):
    # Gate on but no resolvable origin -> still no-op. Block the config_loader
    # fallback so the absence of PUBLIC_SITE_URL truly means "no origin".
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "true")
    monkeypatch.setattr(social_publishers, "_canonical_origin", lambda: None)

    assert social_publishers._utm_cta_link("tiktok", "Spring Sale") is None


def test_utm_cta_link_tagged_url_when_enabled_with_origin(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "1")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com/")

    link = social_publishers._utm_cta_link("tiktok", "Spring Sale 2026")

    assert link is not None
    # Trailing slash on origin is stripped before composing the URL.
    assert link.startswith("https://example.com/?")
    assert "utm_source=tiktok" in link
    assert "utm_medium=social" in link
    # Campaign is slugified: lowercased, non-alnum runs -> single hyphen.
    assert "utm_campaign=spring-sale-2026" in link


def test_utm_cta_link_respects_source_and_medium_overrides(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "yes")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")
    monkeypatch.setenv("THO_UTM_SOURCE", "ig")
    monkeypatch.setenv("THO_UTM_MEDIUM", "paid_social")

    link = social_publishers._utm_cta_link("instagram_reels", None)

    assert link is not None
    assert "utm_source=ig" in link
    assert "utm_medium=paid_social" in link
    # Empty campaign falls back to the "ad-studio" default token.
    assert "utm_campaign=ad-studio" in link
