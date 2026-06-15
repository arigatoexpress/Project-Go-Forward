# ruff: noqa: E402
"""Tests for tools/local_seo_competitor.py.

Run: python -m pytest tests/test_local_seo_competitor.py -v
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.local_seo_competitor import (
    Competitor,
    _recommendations,
    _score_competitor,
    _score_tho,
    _tho_signals,
    analyze_local_seo,
    load_competitors,
)


def _fake_business(overrides: dict | None = None) -> dict:
    base = {
        "name": "Texas Home Outlet",
        "phone": "(281) 324-3020",
        "address": "10685 FM 1960 East, Huffman, TX 77336",
        "street": "10685 FM 1960 East",
        "city": "Huffman",
        "state": "TX",
        "zip": "77336",
        "website": "https://www.texashomeoutlet.com",
        "price_range": "$$",
        "logo_url": "/tex-icon.svg",
        "hours_structured": [{"days": ["Monday"], "opens": "09:00", "closes": "18:00"}],
        "area_served": ["Huffman", "Humble", "Atascocita"],
        "geo": None,
        "same_as": [],
    }
    if overrides:
        base.update(overrides)
    return base


def _patch_config(monkeypatch, business: dict):
    """Monkeypatch config_loader so the tool sees a deterministic business dict."""
    import config_loader

    monkeypatch.setattr(config_loader, "_config", {"business": business})


@pytest.fixture
def sample_competitors():
    return [
        Competitor(
            name="Strong Competitor",
            domain="example.com",
            review_score=4.5,
            review_count=200,
            signals={"has_gbp": True, "has_city_pages": True, "nap_consistent": True},
        ),
        Competitor(
            name="Weak Competitor",
            review_score=3.0,
            review_count=5,
            signals={"has_gbp": False, "has_city_pages": False},
        ),
    ]


def test_load_default_competitors_returns_built_in_registry():
    competitors = load_competitors()
    names = {c.name for c in competitors}
    assert "Clayton Homes of Humble" in names
    assert all(isinstance(c, Competitor) for c in competitors)


def test_load_competitors_from_json(tmp_path, monkeypatch):
    registry = [
        {
            "name": "Custom Competitor",
            "domain": "custom.example.com",
            "cities_served": ["Humble"],
            "review_score": 4.1,
            "review_count": 60,
            "signals": {"has_gbp": True},
        }
    ]
    path = tmp_path / "competitors.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    monkeypatch.setenv("COMPETITOR_REGISTRY_PATH", str(path))
    competitors = load_competitors()
    assert len(competitors) == 1
    assert competitors[0].name == "Custom Competitor"
    assert competitors[0].review_score == 4.1


def test_load_competitors_json_must_be_list(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text('{"name": "not a list"}', encoding="utf-8")
    monkeypatch.setenv("COMPETITOR_REGISTRY_PATH", str(path))
    with pytest.raises(ValueError, match="JSON list"):
        load_competitors()


def test_competitor_to_dict_roundtrip():
    c = Competitor(name="Test", cities_served=["Humble"], signals={"has_gbp": True})
    assert Competitor(**c.to_dict()) == c


def test_score_competitor_full_signals():
    c = Competitor(
        name="Full",
        domain="example.com",
        review_score=4.5,
        review_count=100,
        signals={"has_gbp": True, "has_city_pages": True, "nap_consistent": True},
    )
    assert _score_competitor(c) == 100


def test_score_competitor_no_signals():
    c = Competitor(name="Empty")
    assert _score_competitor(c) == 0


def test_score_tho_complete(monkeypatch):
    biz = _fake_business(
        {
            "geo": {"latitude": 30.0, "longitude": -95.0},
            "same_as": ["https://business.google.com/tho", "https://facebook.com/tho"],
            "area_served": ["A", "B", "C", "D", "E", "F"],
        }
    )
    _patch_config(monkeypatch, biz)
    signals = _tho_signals()
    assert _score_tho(signals) == 100


def test_score_tho_missing_gbp_and_geo(monkeypatch):
    biz = _fake_business({"area_served": []})
    _patch_config(monkeypatch, biz)
    signals = _tho_signals()
    # No geo, no gbp, no city pages, no social = 0
    # logo + price + hours = 15, website = 5 -> 20
    assert _score_tho(signals) == 20


def test_tho_signals_detects_gbp_and_social(monkeypatch):
    biz = _fake_business(
        {
            "same_as": [
                "https://business.google.com/tho",
                "https://instagram.com/tho",
            ]
        }
    )
    _patch_config(monkeypatch, biz)
    signals = _tho_signals()
    assert signals["has_gbp"] is True
    assert signals["has_instagram"] is True
    assert signals["has_facebook"] is False


def test_recommendations_include_gbp_and_geo_when_missing(sample_competitors):
    tho = {"has_gbp": False, "has_geo": False, "city_page_count": 0, "has_facebook": False}
    recs = _recommendations(tho, sample_competitors)
    categories = {r["category"] for r in recs}
    assert "Google Business Profile" in categories
    assert "Geo coordinates" in categories


def test_recommendations_include_review_velocity_for_strong_competitors(
    sample_competitors,
):
    tho = {"has_gbp": True, "has_geo": True, "city_page_count": 6, "has_facebook": True}
    recs = _recommendations(tho, sample_competitors)
    assert any(r["category"] == "Reviews" for r in recs)


def test_recommendations_no_review_rec_when_competitors_weak():
    weak = [Competitor(name="Weak", review_score=3.0, review_count=5)]
    tho = {"has_gbp": True, "has_geo": True, "city_page_count": 6, "has_facebook": True}
    recs = _recommendations(tho, weak)
    assert not any(r["category"] == "Reviews" for r in recs)


def test_analyze_local_seo_structure(monkeypatch):
    _patch_config(monkeypatch, _fake_business())
    report = analyze_local_seo()
    assert "generated_at" in report
    assert report["market"] == "Huffman, TX"
    assert "tho" in report
    assert "competitors" in report
    assert isinstance(report["recommendations"], list)
    assert isinstance(report["market_average_score"], float)


def test_analyze_local_seo_gap_calculation(monkeypatch, sample_competitors):
    _patch_config(monkeypatch, _fake_business({"area_served": []}))
    report = analyze_local_seo()
    # Default competitors have known scores; gap should be computed
    assert isinstance(report["gap_vs_market"], float)
    assert report["tho"]["score"] < report["market_average_score"]


def test_analyze_local_seo_custom_registry(monkeypatch, tmp_path):
    _patch_config(monkeypatch, _fake_business())
    registry = [asdict(Competitor(name="Only One", review_score=4.0, review_count=50))]
    path = tmp_path / "competitors.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    report = analyze_local_seo(path=str(path))
    assert len(report["competitors"]) == 1
    assert report["competitors"][0]["name"] == "Only One"


def test_city_page_count_drives_recommendation_priority(monkeypatch):
    biz = _fake_business({"area_served": ["Huffman"], "has_gbp": True, "has_geo": True})
    _patch_config(monkeypatch, biz)
    recs = _recommendations(
        _tho_signals(),
        [Competitor(name="NoReviewCompetitor")],
    )
    city_rec = next(r for r in recs if r["category"] == "City landing pages")
    assert city_rec["priority"] == "high"
