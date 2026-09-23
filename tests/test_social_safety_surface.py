"""Structural guarantees for Ad Studio's draft-only social workflow."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402


def test_draft_and_live_publish_routes_are_separate(monkeypatch):
    _client, main, _db, _logger = create_client(monkeypatch)
    methods_by_path = {
        route.path: set(getattr(route, "methods", None) or ())
        for route in main.app.routes
        if getattr(route, "path", None)
    }

    assert "POST" in methods_by_path["/api/marketing/publish"]
    assert "/api/marketing/social-readiness" not in methods_by_path
    assert "POST" in methods_by_path["/api/marketing/schedule"]


def test_draft_builder_has_no_social_provider_transport_or_credentials():
    source_path = REPO_ROOT / "tools" / "social_publishers.py"
    source = source_path.read_text()
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    assert "requests" not in imported_modules
    assert "_publish_tiktok_video" not in functions
    assert "_publish_instagram_reel" not in functions
    for forbidden in (
        "THO_SOCIAL_PUBLISH_ENABLED",
        "TIKTOK_ACCESS_TOKEN",
        "META_ACCESS_TOKEN",
        "INSTAGRAM_BUSINESS_ACCOUNT_ID",
        "open.tiktokapis.com",
        "graph.facebook.com",
        "media_publish",
    ):
        assert forbidden not in source


def test_legacy_public_social_publish_asset_bridge_is_removed():
    assert not (REPO_ROOT / "tools" / "marketing_assets.py").exists()


def test_marketing_tools_has_no_legacy_tiktok_runtime_handler():
    source = (REPO_ROOT / "tools" / "marketing_tools.py").read_text()

    assert "class TikTokHandler" not in source
    assert "tiktok_handler =" not in source
    assert "TIKTOK_ACCESS_TOKEN" not in source
    assert "business-api.tiktok.com" not in source


def test_runtime_template_does_not_advertise_social_publish_credentials():
    source = (REPO_ROOT / ".env.example").read_text()

    for forbidden in (
        "THO_SOCIAL_PUBLISH_ENABLED",
        "TIKTOK_ACCESS_TOKEN",
        "TIKTOK_PRIVACY_LEVEL",
        "META_ACCESS_TOKEN",
        "META_GRAPH_VERSION",
        "INSTAGRAM_BUSINESS_ACCOUNT_ID",
    ):
        assert forbidden not in source
