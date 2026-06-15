"""Local SEO competitor intelligence for Texas Home Outlet.

Provides a structured registry of known manufactured-home competitors in the
Houston / north-east Houston market plus a scoring engine that compares THO's
local-SEO signals against those competitors and returns prioritized,
actionable recommendations.

No live SERP scraping is performed — the analysis is deterministic and
config-driven so it can run offline, in tests, and in air-gapped environments.
Operators can override the competitor registry via ``COMPETITOR_REGISTRY_PATH``
(pointing at a JSON file) without touching source code.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from config_loader import (
    business_address,
    business_city,
    business_name,
    business_phone,
    business_state,
    get_business,
)


@dataclass
class Competitor:
    """A local competitor profile used for gap analysis.

    All fields are intentionally conservative/factual. Review counts and
    scores are illustrative estimates for the scoring model; refresh them
    from public sources (GBP, Yelp, BBB) as needed.
    """

    name: str
    domain: str | None = None
    cities_served: list[str] = field(default_factory=list)
    gbp_url: str | None = None
    review_score: float | None = None  # 0-5
    review_count: int = 0
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Built-in registry for the THO primary market. Only include competitors we
# can reasonably verify exist in the market. Keep this list factual and
# conservative — it is a starting point, not a scraped SERP snapshot.
_DEFAULT_COMPETITORS: list[Competitor] = [
    Competitor(
        name="Clayton Homes of Humble",
        domain="www.claytonhomes.com",
        cities_served=["Humble", "Atascocita", "Kingwood", "Huffman"],
        review_score=4.2,
        review_count=120,
        strengths=["National brand recognition", "Large advertising budget", "Multiple locations"],
        weaknesses=["Less personalized service", "Longer delivery times"],
        signals={"has_gbp": True, "has_city_pages": True, "nap_consistent": True},
    ),
    Competitor(
        name="Palm Harbor Homes",
        domain="www.palmharbor.com",
        cities_served=["Houston", "Humble", "Conroe"],
        review_score=4.0,
        review_count=85,
        strengths=["Customizable floor plans", "Strong financing options"],
        weaknesses=["Higher prices", "Limited lot models"],
        signals={"has_gbp": True, "has_city_pages": True, "nap_consistent": True},
    ),
    Competitor(
        name="Schult Homes of Houston",
        domain="www.schult.com",
        cities_served=["Houston", "Baytown", "Crosby"],
        review_score=None,
        review_count=0,
        strengths=["Quality construction reputation"],
        weaknesses=["Smaller local presence", "Weak web presence"],
        signals={"has_gbp": False, "has_city_pages": False, "nap_consistent": False},
    ),
]


def _load_registry_path() -> str | None:
    return os.environ.get("COMPETITOR_REGISTRY_PATH")


def load_competitors(path: str | None = None) -> list[Competitor]:
    """Load competitors from JSON or fall back to the built-in registry.

    JSON format is a list of Competitor-compatible objects, e.g.:

    [
      {
        "name": "Clayton Homes of Humble",
        "domain": "www.claytonhomes.com",
        "cities_served": ["Humble", "Atascocita"],
        "review_score": 4.2,
        "review_count": 120,
        "signals": {"has_gbp": true, "has_city_pages": true}
      }
    ]
    """
    p = path or _load_registry_path()
    if p and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("Competitor registry must be a JSON list")
        return [Competitor(**item) for item in data]
    return list(_DEFAULT_COMPETITORS)


def _tho_signals() -> dict[str, Any]:
    """Extract THO's local-SEO signals from config.yaml."""
    biz = get_business() or {}
    geo = biz.get("geo") or {}
    same_as = biz.get("same_as") or []
    area_served = biz.get("area_served") or []

    return {
        "name": business_name(),
        "phone": business_phone(),
        "address": business_address(),
        "city": business_city(),
        "state": business_state(),
        "website": biz.get("website"),
        "has_geo": bool(geo.get("latitude") and geo.get("longitude")),
        "has_gbp": any("business.google.com" in str(u) for u in same_as),
        "has_facebook": any("facebook.com" in str(u) for u in same_as),
        "has_instagram": any("instagram.com" in str(u) for u in same_as),
        "area_served": area_served,
        "city_page_count": len(area_served),
        "has_logo": bool(biz.get("logo_url")),
        "has_price_range": bool(biz.get("price_range")),
        "has_hours": bool(biz.get("hours_structured")),
    }


def _score_competitor(competitor: Competitor) -> int:
    """Score a competitor out of 100 based on known local-SEO signals."""
    score = 0
    signals = competitor.signals or {}
    if signals.get("has_gbp"):
        score += 25
    if signals.get("has_city_pages"):
        score += 20
    if signals.get("nap_consistent"):
        score += 15
    if competitor.review_score and competitor.review_score >= 4.0:
        score += 20
    if competitor.review_count and competitor.review_count >= 50:
        score += 10
    if competitor.domain:
        score += 10
    return min(score, 100)


def _score_tho(signals: dict[str, Any]) -> int:
    """Score THO out of 100 based on config-driven local-SEO signals."""
    score = 0
    if signals["has_geo"]:
        score += 25
    if signals["has_gbp"]:
        score += 25
    if signals["city_page_count"] >= 5:
        score += 20
    elif signals["city_page_count"] >= 1:
        score += 10
    if signals["has_facebook"] or signals["has_instagram"]:
        score += 10
    if signals["has_logo"] and signals["has_price_range"] and signals["has_hours"]:
        score += 15
    if signals["website"]:
        score += 5
    return min(score, 100)


def _recommendations(
    tho: dict[str, Any], competitors: list[Competitor]
) -> list[dict[str, Any]]:
    """Generate prioritized recommendations from signal gaps."""
    recs: list[dict[str, Any]] = []

    if not tho.get("has_gbp"):
        recs.append(
            {
                "priority": "critical",
                "category": "Google Business Profile",
                "title": "Create and verify a Google Business Profile",
                "impact": (
                    "Single biggest local-pack ranking factor for "
                    "manufactured-home queries"
                ),
                "action": (
                    "Visit business.google.com, claim/verify the Huffman location, "
                    "add photos, hours, services, and the website URL."
                ),
            }
        )

    if not tho.get("has_geo"):
        recs.append(
            {
                "priority": "critical",
                "category": "Geo coordinates",
                "title": "Add exact showroom lat/lng to config.yaml",
                "impact": "Required for LocalBusiness geo JSON-LD and map placement",
                "action": (
                    "Copy lat/lng from the verified GBP and set "
                    "business.geo.latitude / business.geo.longitude."
                ),
            }
        )

    if tho.get("city_page_count", 0) < 5:
        recs.append(
            {
                "priority": "high",
                "category": "City landing pages",
                "title": f"Expand city pages ({tho.get('city_page_count', 0)} served cities)",
                "impact": "Each page captures 'manufactured homes in {city}' intent",
                "action": (
                    "Add /manufactured-homes-in-{city}-tx pages for "
                    "remaining served cities."
                ),
            }
        )

    if not (tho.get("has_facebook") or tho.get("has_instagram")):
        recs.append(
            {
                "priority": "high",
                "category": "Social citations",
                "title": "Add Facebook and Instagram sameAs URLs",
                "impact": "Strengthens Google's knowledge graph and citation consistency",
                "action": (
                    "Set business.same_as[] in config.yaml with active "
                    "social profile URLs."
                ),
            }
        )

    strong_reviews = [
        c
        for c in competitors
        if (c.review_score or 0) >= 4.0 and c.review_count >= 50
    ]
    if strong_reviews:
        top = max(strong_reviews, key=lambda c: c.review_count)
        recs.append(
            {
                "priority": "high",
                "category": "Reviews",
                "title": f"Build review velocity to match {top.name}",
                "impact": (
                    f"{top.name} shows {top.review_count} reviews at "
                    f"{top.review_score} stars"
                ),
                "action": (
                    "Add a post-sale review request workflow (email/SMS) "
                    "and reply to all existing reviews."
                ),
            }
        )

    return recs


def analyze_local_seo(path: str | None = None) -> dict[str, Any]:
    """Run a complete local-SEO competitor analysis.

    Returns a dict with THO's score, competitor scores, market average,
    gap analysis, and prioritized recommendations.
    """
    competitors = load_competitors(path)
    tho = _tho_signals()
    tho_score = _score_tho(tho)

    competitor_scores = [
        {
            "name": c.name,
            "domain": c.domain,
            "score": _score_competitor(c),
            "review_score": c.review_score,
            "review_count": c.review_count,
            "signals": c.signals,
        }
        for c in competitors
    ]

    avg_competitor = (
        round(sum(s["score"] for s in competitor_scores) / len(competitor_scores), 1)
        if competitor_scores
        else 0.0
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "market": f"{business_city()}, {business_state()}",
        "tho": {"score": tho_score, "signals": tho},
        "competitors": competitor_scores,
        "market_average_score": avg_competitor,
        "gap_vs_market": round(avg_competitor - tho_score, 1),
        "recommendations": _recommendations(tho, competitors),
    }
