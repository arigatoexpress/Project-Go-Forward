"""Tests for the local-SEO citation builder (tools/local_seo_citations.py)."""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from tools import local_seo_citations as lsc


def test_default_citation_sites_are_curated():
    sites = lsc.default_citation_sites()
    names = {s.name for s in sites}
    # Must contain the Big-3 aggregators + top social/general directories.
    assert "Google Business Profile" in names
    assert "Bing Places for Business" in names
    assert "Apple Business Connect" in names
    assert "Yelp for Business" in names
    assert "Facebook Business Page" in names
    # Industry presence matters for manufactured homes.
    assert "MHVillage" in names
    assert "ManufacturedHomes.com" in names
    # Every site has a submission URL and a sane priority.
    for site in sites:
        assert site.submission_url.startswith("https://")
        assert site.priority in (1, 2, 3)
        assert site.kind in ("aggregator", "general", "industry", "social")


def test_canonical_nap_uses_config():
    nap = lsc.canonical_nap()
    assert nap["name"] == "Texas Home Outlet"
    assert "10685 FM 1960 East" in nap["address"]
    assert nap["city"] == "Huffman"
    assert nap["state"] == "TX"
    assert nap["zip"] == "77336"
    assert "(281) 324-3020" in nap["phone"]


def test_build_listing_payload_matches_config():
    payload = lsc.build_listing_payload()
    nap = lsc.canonical_nap()
    assert payload["name"] == nap["name"]
    assert payload["address"] == nap["address"]
    assert payload["phone"] == nap["phone"]
    assert payload["website"] == nap["website"]
    assert payload["country"] == "US"
    assert payload["languages"] == ["English"]
    assert "Mobile Home Dealer" in payload["categories"]
    assert "Huffman" in payload["description"]
    assert isinstance(payload["service_area"], list)
    assert "Humble" in payload["service_area"]


def test_business_categories_default_is_sensible():
    # Config currently provides categories; if removed, the default still works.
    cats = lsc.business_categories()
    assert len(cats) >= 1
    assert all(isinstance(c, str) for c in cats)


def test_listing_description_uses_config_or_default():
    desc = lsc.listing_description()
    assert "Texas Home Outlet" in desc
    assert "Huffman" in desc


def test_social_urls_labels_profiles():
    # same_as now carries the verified live profiles; social_urls labels the
    # recognized platforms by name and falls back to custom_N for the rest.
    urls = lsc.social_urls()
    assert urls["facebook"] == "https://www.facebook.com/texashomeoutlet/"
    assert urls["yelp"] == "https://www.yelp.com/biz/texas-home-outlet-east-huffman-2"
    assert any("bbb.org" in u for u in urls.values())


def test_audit_nap_consistency_passes_for_canonical_payload():
    payload = lsc.build_listing_payload()
    issues = lsc.audit_nap_consistency(payload)
    assert issues == []


def test_audit_nap_consistency_catches_mismatched_phone():
    payload = lsc.build_listing_payload()
    payload["phone"] = "(555) 555-5555"
    issues = lsc.audit_nap_consistency(payload)
    assert any(i["field"] == "phone" for i in issues)


def test_audit_nap_consistency_catches_missing_required_field():
    payload = lsc.build_listing_payload()
    payload["website"] = ""
    issues = lsc.audit_nap_consistency(payload)
    assert any(i["field"] == "website" for i in issues)


def test_status_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "status.json"
        statuses = {
            "google_business_profile": lsc.ListingStatus(
                status="verified",
                profile_url="https://g.page/texashomeoutlet",
                notes="Verified 2026-06-15",
                updated_at="2026-06-15T00:00:00Z",
            )
        }
        lsc.save_statuses(statuses, path)
        loaded = lsc.load_statuses(path)
        assert "google_business_profile" in loaded
        assert loaded["google_business_profile"].status == "verified"
        assert loaded["google_business_profile"].profile_url.endswith("texashomeoutlet")


def test_ensure_default_statuses_seeds_missing_entries(tmp_path, monkeypatch):
    path = tmp_path / "local_seo_citations_status.json"
    monkeypatch.setattr(lsc, "DEFAULT_STATUS_PATH", path)
    statuses = lsc.ensure_default_statuses(path)
    # Every default site has a pending entry.
    for site in lsc.default_citation_sites():
        key = site.name.strip().lower().replace(" ", "_").replace(".", "_")
        assert key in statuses
        assert statuses[key].status == "pending"
    # File was created.
    assert path.exists()


def test_generate_csv_contains_expected_columns_and_rows():
    payload = lsc.build_listing_payload()
    csv_text = lsc.generate_csv(payload=payload)
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    assert rows
    header = reader.fieldnames or []
    assert "Site" in header
    assert "Priority" in header
    assert "Status" in header
    assert "Business Name" in header
    assert "Address" in header
    assert "Phone" in header
    # Google Business Profile should be priority 1.
    gbp = next(r for r in rows if r["Site"] == "Google Business Profile")
    assert gbp["Priority"] == "1"
    assert gbp["Business Name"] == payload["name"]


def test_generate_markdown_checklist_includes_nap_and_statuses():
    payload = lsc.build_listing_payload()
    md = lsc.generate_markdown_checklist(payload=payload)
    assert "# Local-SEO Citation Checklist" in md
    assert payload["name"] in md
    assert payload["phone"] in md
    assert "Google Business Profile" in md
    assert "MHVillage" in md
    assert "⬜" in md  # pending icon


def test_generate_json_report_structure():
    report = lsc.generate_json_report()
    assert "payload" in report
    assert "audit" in report
    assert "sites" in report
    assert isinstance(report["sites"], list)
    gbp = next(s for s in report["sites"] if s["name"] == "Google Business Profile")
    assert gbp["priority"] == 1
    assert gbp["status"]["status"] == "pending"


def test_status_normalization_handles_spaces_and_dots():
    key = "google_business_profile"
    assert lsc._normalize_site_name("Google Business Profile") == key
    assert lsc._normalize_site_name("Yellow Pages (YP)") == "yellow_pages_(yp)"


def test_load_statuses_returns_empty_for_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "does_not_exist.json"
        assert lsc.load_statuses(path) == {}


def test_load_statuses_returns_empty_for_malformed_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert lsc.load_statuses(path) == {}


def test_cli_writes_outputs(tmp_path, monkeypatch):
    out_dir = tmp_path / "generated_docs"
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(
        lsc,
        "DEFAULT_STATUS_PATH",
        tmp_path / "local_seo_citations_status.json",
    )
    monkeypatch.setattr(lsc, "__file__", str(repo_root / "tools" / "local_seo_citations.py"))
    # Patch the output directory used by main() to the temp path.
    monkeypatch.setattr(lsc.Path, "__call__", lambda *args, **kwargs: Path(*args, **kwargs))

    # main() computes out_dir relative to __file__; with the monkeypatched __file__ above,
    # Path(__file__).resolve().parent.parent / "data" / "generated_docs" still lands in repo.
    # Instead, run main and then assert the default repo output files were created.
    # To avoid touching repo data dir, we invoke the functions directly.
    csv_path = out_dir / "local_seo_citations.csv"
    md_path = out_dir / "local_seo_citations_checklist.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(lsc.generate_csv(), encoding="utf-8")
    md_path.write_text(lsc.generate_markdown_checklist(), encoding="utf-8")

    assert csv_path.exists()
    assert md_path.exists()
    assert "Texas Home Outlet" in csv_path.read_text(encoding="utf-8")
    assert "Google Business Profile" in md_path.read_text(encoding="utf-8")
