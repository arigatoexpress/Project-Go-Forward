"""Local-SEO citation builder for Texas Home Outlet.

Builds consistent NAP (name/address/phone) listing payloads and tracks
submission status across the directory / citation sites that matter most for
a single-location manufactured-home dealership. All data is sourced from
config.yaml and stored locally — no directory APIs are called, so no PII
leaves the machine until an operator manually submits it.

Intended uses:
- Generate a CSV bulk-sheet for manual directory uploads / VA handoff.
- Produce a markdown checklist an operator can work through.
- Audit a candidate listing for NAP consistency against the canonical config.
- Power an admin API (wire the optional router into main.py).
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config_loader import (
    business_address,
    business_city,
    business_email,
    business_hours,
    business_name,
    business_phone,
    business_state,
    business_street,
    business_zip,
    get_business,
)

# Canonical status file. Operators can edit this by hand or via the API;
# it is the single source of truth for "which citations are claimed?".
DEFAULT_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "local_seo_citations_status.json"


@dataclass(frozen=True)
class CitationSite:
    """A directory / citation site where THO should have a listing."""

    name: str
    domain: str
    submission_url: str
    priority: int  # 1 = highest
    kind: str  # "aggregator" | "general" | "industry" | "social"
    notes: str = ""
    supported_fields: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ListingStatus:
    """Operator-managed status for one citation site."""

    status: str = "pending"  # pending | claimed | verified | needs_update | not_applicable
    profile_url: str = ""
    notes: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListingStatus:
        return cls(
            status=str(data.get("status", "pending")),
            profile_url=str(data.get("profile_url", "")),
            notes=str(data.get("notes", "")),
            updated_at=str(data.get("updated_at", "")),
        )


# Curated list based on local-pack ranking factors for a U.S. dealership.
# Priority 1 = claim first; priority 2 = high-value secondary; priority 3 = nice-to-have.
# URLs point to the claim/submission landing page where available.
def default_citation_sites() -> list[CitationSite]:
    """Return the canonical citation-site list for THO."""
    return [
        CitationSite(
            name="Google Business Profile",
            domain="business.google.com",
            submission_url="https://business.google.com/create",
            priority=1,
            kind="aggregator",
            notes="Biggest local-SEO unlock. Verify Huffman location, add photos/hours.",
            supported_fields=("name", "address", "phone", "website", "email", "hours", "categories", "geo", "photos"),
        ),
        CitationSite(
            name="Bing Places for Business",
            domain="bingplaces.com",
            submission_url="https://www.bingplaces.com/",
            priority=1,
            kind="aggregator",
            notes="Feeds Bing Maps and ChatGPT search (Bing index).",
            supported_fields=("name", "address", "phone", "website", "email", "hours", "categories"),
        ),
        CitationSite(
            name="Apple Business Connect",
            domain="businessconnect.apple.com",
            submission_url="https://businessconnect.apple.com/",
            priority=1,
            kind="aggregator",
            notes="Powers Apple Maps / Siri local results on iOS.",
            supported_fields=("name", "address", "phone", "website", "hours", "categories"),
        ),
        CitationSite(
            name="Yelp for Business",
            domain="yelp.com",
            submission_url="https://biz.yelp.com/",
            priority=1,
            kind="general",
            notes="High-domain-authority; strong for local pack co-citation signals.",
            supported_fields=("name", "address", "phone", "website", "hours", "categories", "photos"),
        ),
        CitationSite(
            name="Facebook Business Page",
            domain="facebook.com",
            submission_url="https://business.facebook.com/",
            priority=1,
            kind="social",
            notes="Also feeds sameAs / knowledge-graph signals.",
            supported_fields=("name", "address", "phone", "website", "email", "hours", "categories", "photos"),
        ),
        CitationSite(
            name="Better Business Bureau",
            domain="bbb.org",
            submission_url="https://www.bbb.org/get-accredited/",
            priority=2,
            kind="general",
            notes="Trust signal; accreditation is optional but profile is valuable.",
            supported_fields=("name", "address", "phone", "website", "email"),
        ),
        CitationSite(
            name="Yellow Pages (YP)",
            domain="yellowpages.com",
            submission_url="https://www.yellowpages.com/claim",
            priority=2,
            kind="general",
            notes="Legacy directory still used by older buyers and data aggregators.",
            supported_fields=("name", "address", "phone", "website", "hours", "categories"),
        ),
        CitationSite(
            name="MapQuest",
            domain="mapquest.com",
            submission_url="https://business.mapquest.com/",
            priority=2,
            kind="general",
            notes="Navigation / local search citations.",
            supported_fields=("name", "address", "phone", "website", "hours"),
        ),
        CitationSite(
            name="Foursquare for Business",
            domain="foursquare.com",
            submission_url="https://business.foursquare.com/",
            priority=2,
            kind="general",
            notes="Location data powers many third-party apps.",
            supported_fields=("name", "address", "phone", "website", "hours", "categories"),
        ),
        CitationSite(
            name="Nextdoor Business",
            domain="nextdoor.com",
            submission_url="https://business.nextdoor.com/",
            priority=2,
            kind="general",
            notes="Neighborhood-level visibility in Houston suburbs.",
            supported_fields=("name", "address", "phone", "website", "hours", "categories"),
        ),
        CitationSite(
            name="Chamber of Commerce",
            domain="chamberofcommerce.com",
            submission_url="https://www.chamberofcommerce.com/",
            priority=2,
            kind="general",
            notes="Add a free listing; strong backlink + NAP signal.",
            supported_fields=("name", "address", "phone", "website", "email"),
        ),
        CitationSite(
            name="Manta",
            domain="manta.com",
            submission_url="https://www.manta.com/",
            priority=3,
            kind="general",
            notes="Small-business directory; claim existing auto-generated listing.",
            supported_fields=("name", "address", "phone", "website", "email"),
        ),
        CitationSite(
            name="MHVillage",
            domain="mhvillage.com",
            submission_url="https://www.mhvillage.com/",
            priority=2,
            kind="industry",
            notes="Largest manufactured-housing marketplace; dealer directory.",
            supported_fields=("name", "address", "phone", "website", "email", "hours"),
        ),
        CitationSite(
            name="ManufacturedHomes.com",
            domain="manufacturedhomes.com",
            submission_url="https://www.manufacturedhomes.com/",
            priority=2,
            kind="industry",
            notes="Industry directory; may have legacy THO presence to reclaim.",
            supported_fields=("name", "address", "phone", "website", "hours"),
        ),
        CitationSite(
            name="ModularHomes.com",
            domain="modularhomes.com",
            submission_url="https://www.modularhomes.com/",
            priority=3,
            kind="industry",
            notes="Relevant if THO carries modular product lines.",
            supported_fields=("name", "address", "phone", "website"),
        ),
        CitationSite(
            name="Angi",
            domain="angi.com",
            submission_url="https://www.angi.com/companylist/",
            priority=3,
            kind="general",
            notes="Home-services angle (setup/delivery).",
            supported_fields=("name", "address", "phone", "website", "hours"),
        ),
        CitationSite(
            name="Thumbtack",
            domain="thumbtack.com",
            submission_url="https://www.thumbtack.com/pro/",
            priority=3,
            kind="general",
            notes="Pro / contractor listing for setup/delivery services.",
            supported_fields=("name", "address", "phone", "website", "hours"),
        ),
    ]


def _business_config() -> dict[str, Any]:
    return get_business() or {}


def canonical_nap() -> dict[str, str]:
    """Return the single-source NAP block from config.yaml."""
    return {
        "name": business_name(),
        "street": business_street(),
        "city": business_city(),
        "state": business_state(),
        "zip": business_zip(),
        "address": business_address(),
        "phone": business_phone(),
        "email": business_email(),
        "website": _business_config().get("website", ""),
    }


def business_categories() -> list[str]:
    """Return primary/secondary categories from config, with sane defaults."""
    cats = _business_config().get("categories") or []
    if cats:
        return list(cats)
    return ["Mobile Home Dealer", "Manufactured Home Dealer"]


def listing_description() -> str:
    """Return the configured short description or a default local-SEO blurb."""
    desc = _business_config().get("short_description") or ""
    if desc:
        return str(desc).strip()
    return (
        f"{business_name()} is a manufactured and mobile home dealership serving "
        f"{business_city()}, TX and the greater Houston area. Visit our showroom at "
        f"{business_address()} or call {business_phone()}."
    )


def service_area_cities() -> list[str]:
    """Cities from config.area_served, used for service-area fields."""
    return list(_business_config().get("area_served") or [])


def social_urls() -> dict[str, str]:
    """Return social/profile URLs from config.same_as as a labelled map."""
    same_as = list(_business_config().get("same_as") or [])
    out: dict[str, str] = {}
    for url in same_as:
        host = url.lower()
        if "facebook" in host:
            out["facebook"] = url
        elif "instagram" in host:
            out["instagram"] = url
        elif "google" in host or "g.page" in host or "goo.gl" in host:
            out["google_business_profile"] = url
        elif "yelp" in host:
            out["yelp"] = url
        elif "linkedin" in host:
            out["linkedin"] = url
        elif "x.com" in host or "twitter" in host:
            out["twitter"] = url
        else:
            key = f"custom_{len(out)}"
            out[key] = url
    return out


def build_listing_payload() -> dict[str, Any]:
    """Full, consistent listing payload suitable for directory submission."""
    biz = _business_config()
    payload = {
        "name": business_name(),
        "address": business_address(),
        "street": business_street(),
        "city": business_city(),
        "state": business_state(),
        "zip": business_zip(),
        "country": "US",
        "phone": business_phone(),
        "email": business_email(),
        "website": biz.get("website", ""),
        "hours": business_hours(),
        "hours_structured": biz.get("hours_structured", []),
        "description": listing_description(),
        "categories": business_categories(),
        "service_area": service_area_cities(),
        "logo_url": biz.get("logo_url", ""),
        "image_url": biz.get("image_url") or biz.get("logo_url", ""),
        "price_range": biz.get("price_range", ""),
        "year_established": biz.get("year_established") or "",
        "payment_methods": biz.get("payment_methods") or [],
        "languages": ["English"],
        "social_urls": social_urls(),
        "geo": biz.get("geo") or {},
    }
    return payload


def audit_nap_consistency(payload: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Compare a candidate listing payload against the canonical config NAP.

    Returns a list of issues, each with ``field``, ``expected``, ``actual``.
    """
    payload = payload or build_listing_payload()
    canonical = canonical_nap()
    issues: list[dict[str, str]] = []

    required_text = {
        "name": canonical["name"],
        "address": canonical["address"],
        "street": canonical["street"],
        "city": canonical["city"],
        "state": canonical["state"],
        "zip": canonical["zip"],
        "phone": canonical["phone"],
        "website": canonical["website"],
    }
    for field_name, expected in required_text.items():
        actual = payload.get(field_name) or ""
        if not expected:
            issues.append(
                {"field": field_name, "expected": "(set in config.yaml)", "actual": str(actual)}
            )
        elif str(actual).strip() != str(expected).strip():
            issues.append(
                {
                    "field": field_name,
                    "expected": str(expected),
                    "actual": str(actual),
                }
            )
    return issues


def _normalize_site_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace(".", "_")


def load_statuses(path: str | os.PathLike | None = None) -> dict[str, ListingStatus]:
    """Load operator-managed listing statuses from JSON."""
    path = Path(path or DEFAULT_STATUS_PATH)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        _normalize_site_name(k): ListingStatus.from_dict(v)
        for k, v in raw.items()
        if isinstance(v, dict)
    }


def save_statuses(
    statuses: dict[str, ListingStatus],
    path: str | os.PathLike | None = None,
) -> None:
    """Persist operator-managed listing statuses to JSON."""
    path = Path(path or DEFAULT_STATUS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {k: v.to_dict() for k, v in statuses.items()}
    path.write_text(json.dumps(serializable, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_default_statuses(path: str | os.PathLike | None = None) -> dict[str, ListingStatus]:
    """Return current statuses, seeding missing entries as 'pending'."""
    path = Path(path or DEFAULT_STATUS_PATH)
    existing = load_statuses(path)
    sites = default_citation_sites()
    updated = False
    for site in sites:
        key = _normalize_site_name(site.name)
        if key not in existing:
            existing[key] = ListingStatus(status="pending")
            updated = True
    if updated:
        save_statuses(existing, path)
    return existing


def _status_for_site(site: CitationSite, statuses: dict[str, ListingStatus]) -> ListingStatus:
    return statuses.get(_normalize_site_name(site.name), ListingStatus(status="pending"))


def generate_csv(
    sites: list[CitationSite] | None = None,
    payload: dict[str, Any] | None = None,
    statuses: dict[str, ListingStatus] | None = None,
) -> str:
    """Generate a CSV bulk-sheet for directory upload / VA handoff."""
    sites = sites or default_citation_sites()
    payload = payload or build_listing_payload()
    statuses = statuses or ensure_default_statuses()

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "Priority",
            "Site",
            "Domain",
            "Kind",
            "Submission URL",
            "Status",
            "Profile URL",
            "Business Name",
            "Address",
            "Phone",
            "Website",
            "Email",
            "Categories",
            "Description",
            "Notes",
        ]
    )
    for site in sorted(sites, key=lambda s: (s.priority, s.name)):
        status = _status_for_site(site, statuses)
        writer.writerow(
            [
                site.priority,
                site.name,
                site.domain,
                site.kind,
                site.submission_url,
                status.status,
                status.profile_url,
                payload["name"],
                payload["address"],
                payload["phone"],
                payload["website"],
                payload["email"],
                "; ".join(payload.get("categories", [])),
                payload.get("description", ""),
                site.notes + (" | " + status.notes if status.notes else ""),
            ]
        )
    return out.getvalue()


def generate_markdown_checklist(
    sites: list[CitationSite] | None = None,
    payload: dict[str, Any] | None = None,
    statuses: dict[str, ListingStatus] | None = None,
) -> str:
    """Generate a human-readable checklist for manual citation building."""
    sites = sites or default_citation_sites()
    payload = payload or build_listing_payload()
    statuses = statuses or ensure_default_statuses()

    lines = [
        "# Local-SEO Citation Checklist",
        "",
        f"**Business:** {payload['name']}",
        f"**Address:** {payload['address']}",
        f"**Phone:** {payload['phone']}",
        f"**Website:** {payload['website']}",
        f"**Email:** {payload['email']}",
        f"**Hours:** {payload['hours']}",
        f"**Categories:** {', '.join(payload.get('categories', []))}",
        "",
        "Use the exact NAP above on every listing. Inconsistent name, address, or phone "
        "is the #1 reason local rankings drop.",
        "",
    ]
    for site in sorted(sites, key=lambda s: (s.priority, s.name)):
        status = _status_for_site(site, statuses)
        icon = {"verified": "✅", "claimed": "🟡", "needs_update": "🔧", "not_applicable": "➖"}.get(
            status.status, "⬜"
        )
        lines.append(f"{icon} **[P{site.priority}] {site.name}** ({site.domain})")
        lines.append(f"   - Submit/claim: {site.submission_url}")
        if status.profile_url:
            lines.append(f"   - Profile: {status.profile_url}")
        lines.append(f"   - Status: `{status.status}`")
        if site.notes:
            lines.append(f"   - Note: {site.notes}")
        if status.notes:
            lines.append(f"   - Operator note: {status.notes}")
        lines.append("")
    return "\n".join(lines)


def generate_json_report(
    sites: list[CitationSite] | None = None,
    payload: dict[str, Any] | None = None,
    statuses: dict[str, ListingStatus] | None = None,
) -> dict[str, Any]:
    """Machine-readable report of listing payload + per-site status."""
    sites = sites or default_citation_sites()
    payload = payload or build_listing_payload()
    statuses = statuses or ensure_default_statuses()

    return {
        "payload": payload,
        "audit": audit_nap_consistency(payload),
        "sites": [
            {
                "name": site.name,
                "domain": site.domain,
                "priority": site.priority,
                "kind": site.kind,
                "submission_url": site.submission_url,
                "supported_fields": list(site.supported_fields),
                "status": _status_for_site(site, statuses).to_dict(),
            }
            for site in sorted(sites, key=lambda s: (s.priority, s.name))
        ],
    }


def main() -> None:
    """CLI: write the CSV and markdown checklist to ``data/generated_docs/``."""
    payload = build_listing_payload()
    issues = audit_nap_consistency(payload)
    if issues:
        print("NAP consistency issues detected (config vs payload):")
        for issue in issues:
            print(f"  {issue['field']}: expected={issue['expected']!r} actual={issue['actual']!r}")
        print()

    statuses = ensure_default_statuses()
    out_dir = Path(__file__).resolve().parent.parent / "data" / "generated_docs"
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "local_seo_citations.csv"
    csv_path.write_text(generate_csv(payload=payload, statuses=statuses), encoding="utf-8")
    print(f"Wrote CSV: {csv_path}")

    md_path = out_dir / "local_seo_citations_checklist.md"
    md_path.write_text(generate_markdown_checklist(payload=payload, statuses=statuses), encoding="utf-8")
    print(f"Wrote checklist: {md_path}")


if __name__ == "__main__":
    main()
