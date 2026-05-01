"""
tests/test_image_url_backfill.py

Unit tests for tools/image_url_backfill.py.

Coverage:
  - URL construction for new manufactured homes (manufacturer/floorplan CDN path)
  - URL construction for pre-owned homes (dealer/3522/inventory CDN path)
  - HEAD probe error handling: timeout and 404 both handled gracefully
  - resolve_candidate_url: catalog match, Firestore-field fallback, skip when no IDs
  - process_row: skipped_no_ids sentinel when resolve returns None
  - JSONL output via run() in dry-run mode (Firestore stubbed)
"""

import importlib.util
import json
import os
import sys
import types

import pytest
import requests


# ─── Load tools/image_url_backfill.py directly, bypassing tools/__init__.py ──
# tools/__init__.py eagerly imports document_tools (pypdf) which is not installed
# in the test environment. We load the module by file path to avoid that chain.

def _stub_google_firestore():
    google_mod = sys.modules.get("google") or types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    fs_mod = types.ModuleType("google.cloud.firestore")
    fs_mod.Client = object
    fs_mod.Query = types.SimpleNamespace(DESCENDING="DESCENDING")
    google_mod.cloud = cloud_mod
    cloud_mod.firestore = fs_mod
    sys.modules.setdefault("google", google_mod)
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.firestore"] = fs_mod


_stub_google_firestore()

# Inject the tools directory as a discoverable module without running __init__
_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "tools")
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Pre-register a stub `tools` package so Python doesn't run tools/__init__.py
if "tools" not in sys.modules:
    _tools_stub = types.ModuleType("tools")
    _tools_stub.__path__ = [_TOOLS_DIR]
    _tools_stub.__package__ = "tools"
    sys.modules["tools"] = _tools_stub

# Load asset_scraper directly (no problematic deps)
def _load_direct(rel_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        os.path.join(_REPO_ROOT, rel_path),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_load_direct("tools/asset_scraper.py", "tools.asset_scraper")
_iub = _load_direct("tools/image_url_backfill.py", "tools.image_url_backfill")

# Pull names directly from the loaded module
CDN_BASE = _iub.CDN_BASE
DEALER_ID = _iub.DEALER_ID
build_new_home_url = _iub.build_new_home_url
build_preowned_url = _iub.build_preowned_url
probe_url = _iub.probe_url
process_row = _iub.process_row
resolve_candidate_url = _iub.resolve_candidate_url
run = _iub.run

from tools.image_url_backfill import (  # noqa: E402 — verify the names are importable
    CDN_BASE,
    DEALER_ID,
    build_new_home_url,
    build_preowned_url,
    probe_url,
    process_row,
    resolve_candidate_url,
    run,
)


# ─── URL construction ────────────────────────────────────────────────────────


class TestBuildNewHomeUrl:
    def test_default_filename(self):
        url = build_new_home_url("3335", "225053")
        assert url == f"{CDN_BASE}/manufacturer/3335/floorplan/225053/1.jpg"

    def test_custom_filename(self):
        url = build_new_home_url("3335", "225053", "floor-plans-SMALL.jpg")
        assert url == f"{CDN_BASE}/manufacturer/3335/floorplan/225053/floor-plans-SMALL.jpg"

    def test_different_manufacturer(self):
        url = build_new_home_url("1944", "1383")
        assert url == f"{CDN_BASE}/manufacturer/1944/floorplan/1383/1.jpg"

    def test_cdn_base_is_cloudfront(self):
        url = build_new_home_url("3335", "225053")
        assert url.startswith("https://d132mt2yijm03y.cloudfront.net/")


class TestBuildPreownedUrl:
    def test_default_filename(self):
        url = build_preowned_url("44490")
        assert url == f"{CDN_BASE}/dealer/3522/inventory/44490/1.jpg"

    def test_dealer_id_is_3522(self):
        url = build_preowned_url("99999")
        assert f"/dealer/{DEALER_ID}/inventory/" in url
        assert DEALER_ID == "3522"

    def test_custom_filename(self):
        url = build_preowned_url("44491", "floor-plans-SMALL.jpg")
        assert url == f"{CDN_BASE}/dealer/3522/inventory/44491/floor-plans-SMALL.jpg"


# ─── HEAD probe error handling ────────────────────────────────────────────────


class TestProbeUrl:
    def test_200_response(self, requests_mock):
        url = "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225053/1.jpg"
        requests_mock.head(url, status_code=200, headers={
            "Content-Length": "54321",
            "Content-Type": "image/jpeg",
        })
        result = probe_url(url)
        assert result["head_status"] == 200
        assert result["content_length"] == "54321"
        assert result["content_type"] == "image/jpeg"

    def test_404_response(self, requests_mock):
        url = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/1.jpg"
        requests_mock.head(url, status_code=404)
        result = probe_url(url)
        assert result["head_status"] == 404
        assert result["content_length"] is None

    def test_timeout_returns_gracefully(self, requests_mock):
        url = "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225053/slow.jpg"
        requests_mock.head(url, exc=requests.Timeout)
        result = probe_url(url)
        assert result["head_status"] == "timeout"
        assert result["content_length"] is None
        assert result["content_type"] is None

    def test_connection_error_returns_gracefully(self, requests_mock):
        url = "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225053/err.jpg"
        requests_mock.head(url, exc=requests.ConnectionError("refused"))
        result = probe_url(url)
        assert "connection_error" in str(result["head_status"])

    def test_retries_once_on_timeout(self, requests_mock):
        url = "https://d132mt2yijm03y.cloudfront.net/retry.jpg"
        requests_mock.head(url, exc=requests.Timeout)
        result = probe_url(url)
        # Two attempts were made; still returns sentinel
        assert result["head_status"] == "timeout"
        assert requests_mock.call_count == 2


# ─── resolve_candidate_url ───────────────────────────────────────────────────


class TestResolveCandidateUrl:
    def test_known_catalog_model_returns_first_image(self):
        row = {
            "id": "abc123",
            "model_name": "The Big Steve",
            "manufacturer": "New Vision Manufacturing",
            "is_new": True,
        }
        url = resolve_candidate_url(row)
        assert url is not None
        # First image for Big Steve (plan 225053) should be in the URL
        assert "225053" in url

    def test_preowned_numeric_id_not_in_catalog_uses_dealer_path(self):
        row = {
            "id": "99999",
            "model_name": "Unknown Vintage Model ZXQABC 9000",
            "manufacturer": "Pre-Owned",
            "is_new": False,
        }
        url = resolve_candidate_url(row)
        assert url == f"{CDN_BASE}/dealer/3522/inventory/99999/1.jpg"

    def test_preowned_non_numeric_id_returns_none(self):
        row = {
            "id": "firestore-uuid-abc-123",
            "model_name": "Unknown Model Totally New ZXQABC",
            "manufacturer": "Unknown",
            "is_new": True,
        }
        result = resolve_candidate_url(row)
        assert result is None

    def test_row_with_manufacturer_id_and_plan_id_fields(self):
        row = {
            "id": "some-doc-id",
            "model_name": "Unknown Future Model",
            "manufacturer": "Some Mfg",
            "manufacturer_id": "9999",
            "plan_id": "12345",
            "is_new": True,
        }
        url = resolve_candidate_url(row)
        assert url == f"{CDN_BASE}/manufacturer/9999/floorplan/12345/1.jpg"

    def test_big_blue_in_catalog(self):
        row = {
            "id": "44490",
            "model_name": "Big Blue",
            "manufacturer": "Pre-Owned",
            "is_new": False,
        }
        url = resolve_candidate_url(row)
        # Big Blue is in the asset catalog — should use catalog's URL, not dealer path
        assert url is not None
        assert "cloudfront.net" in url

    def test_empty_model_name_preowned_numeric_id(self):
        row = {
            "id": "55555",
            "model_name": "",
            "manufacturer": "Pre-Owned",
            "is_new": False,
        }
        url = resolve_candidate_url(row)
        assert url == f"{CDN_BASE}/dealer/3522/inventory/55555/1.jpg"


# ─── process_row ─────────────────────────────────────────────────────────────


class TestProcessRow:
    def test_skipped_no_ids_sentinel(self):
        row = {
            "id": "firestore-uuid-999",
            "model_name": "Completely Unknown Model ZXQABC",
            "manufacturer": "Unknown",
            "is_new": True,
        }
        # No network call should occur since URL cannot be derived
        result = process_row(row)
        assert result["candidate_url"] is None
        assert result["head_status"] == "skipped_no_ids"
        assert result["inventory_id"] == "firestore-uuid-999"

    def test_known_model_probes_url(self, requests_mock):
        first_image_url = (
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-kit-1.jpg"
        )
        requests_mock.head(first_image_url, status_code=200, headers={
            "Content-Type": "image/jpeg",
            "Content-Length": "102400",
        })
        row = {
            "id": "inv-001",
            "model_name": "The Big Steve",
            "manufacturer": "New Vision Manufacturing",
            "is_new": True,
        }
        result = process_row(row)
        assert result["candidate_url"] == first_image_url
        assert result["head_status"] == 200

    def test_result_keys_present(self, requests_mock):
        url = f"{CDN_BASE}/dealer/3522/inventory/12345/1.jpg"
        requests_mock.head(url, status_code=404)
        row = {"id": "12345", "model_name": "Some Pre-Owned", "manufacturer": "Pre-Owned", "is_new": False}
        result = process_row(row)
        for key in ("inventory_id", "model_name", "manufacturer", "candidate_url",
                    "head_status", "content_length", "content_type"):
            assert key in result, f"Missing key: {key}"


# ─── run() dry-run integration ───────────────────────────────────────────────


class TestRunDryRun:
    """Stub load_inventory_missing_image and verify JSONL output structure."""

    def test_run_produces_jsonl_for_each_row(self, tmp_path, monkeypatch):
        fake_rows = [
            {"id": "44490", "model_name": "Big Blue", "manufacturer": "Pre-Owned", "is_new": False},
            {"id": "inv-uuid-xyz", "model_name": "Totally Unknown ZXQABC", "manufacturer": "Unknown", "is_new": True},
        ]

        # Patch directly on the loaded module object (the tools stub package
        # doesn't carry the submodule as an attribute, so string path fails)
        monkeypatch.setattr(_iub, "load_inventory_missing_image", lambda: fake_rows)

        # Big Blue IS in the asset catalog; its first image uses the manufacturer path.
        # Register that URL as 200; all other HEAD calls return 404.
        big_blue_url = (
            f"{CDN_BASE}/dealer/3522/inventory/44490/floor-plans-SMALL.jpg"
        )

        import requests_mock as rm_lib  # noqa: PLC0415
        with rm_lib.Mocker() as m:
            m.head(big_blue_url, status_code=200, headers={"Content-Type": "image/jpeg"})
            # Allow any other URL to return 404 rather than raising NoMockAddress
            m.head(rm_lib.ANY, status_code=404)

            out_file = tmp_path / "results.jsonl"

            class FakeArgs:
                out = str(out_file)
                apply = False
                yes = False

            results = run(FakeArgs())

        assert len(results) == 2
        lines = out_file.read_text().splitlines()
        assert len(lines) == 2

        parsed = [json.loads(line) for line in lines]
        ids = {r["inventory_id"] for r in parsed}
        assert "44490" in ids
        assert "inv-uuid-xyz" in ids

        unknown = next(r for r in parsed if r["inventory_id"] == "inv-uuid-xyz")
        assert unknown["candidate_url"] is None
        assert unknown["head_status"] == "skipped_no_ids"

    def test_run_empty_inventory_returns_empty(self, monkeypatch):
        monkeypatch.setattr(_iub, "load_inventory_missing_image", lambda: [])

        class FakeArgs:
            out = None
            apply = False
            yes = False

        results = run(FakeArgs())
        assert results == []
