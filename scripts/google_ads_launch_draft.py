#!/usr/bin/env python3
"""Offline validator for the zero-spend Google Ads launch draft.

This command never imports the Google Ads client, reads credentials, or makes a
network request. It exists to keep the checked-in campaign package paused,
policy-safe, attributable, and honest about Google's charging limits.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_DRAFT = Path(__file__).resolve().parents[1] / "config" / "google_ads_launch_draft.json"
CANONICAL_HOST = "www.texashomeoutlet.com"
ALLOWED_LANDING_PATHS = {"/appointments", "/inventory"}
ALLOWED_POSITIVE_MATCH_TYPES = {"EXACT", "PHRASE"}
REVIEWED_BUDGET = {
    "average_daily_usd": 20.0,
    "max_single_day_charge_usd": 40.0,
    "monthly_charge_limit_usd": 608.0,
}
REVIEWED_MAX_CPC_USD = 5.0
REVIEWED_RADIUS_MILES = 50.0
REVIEWED_STOP_LOSS = {
    "evaluation_window_days": 7.0,
    "zero_reachable_leads_spend_usd": 200.0,
    "zero_reachable_leads_clicks": 100.0,
    "max_reachable_lead_cpa_usd": 150.0,
    "minimum_reachable_leads_for_cpa_rule": 3.0,
}
REQUIRED_ACTIVATION_CHECKS = {
    "readiness_audit_green",
    "google_ads_customer_and_manager_ids_confirmed",
    "housing_policy_acknowledged_in_google_ads",
    "ga4_or_gtm_configured_without_duplicate_pageviews",
    "generate_lead_and_schedule_appointment_single_fire_verified",
    "google_ads_conversion_import_verified",
    "search_console_sitemap_accepted",
    "budget_and_stop_loss_explicitly_approved",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def validate_draft(payload: dict[str, Any]) -> list[str]:
    """Return deterministic validation errors without mutating ``payload``."""
    errors: list[str] = []
    campaign = _mapping(payload.get("campaign"))
    activation = _mapping(payload.get("activation_gate"))

    if payload.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if payload.get("mode") != "VALIDATE_ONLY":
        errors.append("mode must remain VALIDATE_ONLY")
    if campaign.get("status") != "PAUSED":
        errors.append("campaign.status must remain PAUSED")
    if campaign.get("channel") != "SEARCH":
        errors.append("campaign.channel must equal SEARCH")
    if activation.get("approved") is not False:
        errors.append("activation_gate.approved must remain false")
    for field in (
        "approved_by",
        "approved_at",
        "approved_average_daily_usd",
        "approved_monthly_charge_limit_usd",
    ):
        if activation.get(field) is not None:
            errors.append(f"activation_gate.{field} must remain null")

    budget = _mapping(campaign.get("budget"))
    daily = _number(budget.get("average_daily_usd"))
    single_day = _number(budget.get("max_single_day_charge_usd"))
    monthly = _number(budget.get("monthly_charge_limit_usd"))
    if daily is None or daily <= 0:
        errors.append("average_daily_usd must be a positive number")
    else:
        if single_day is None or not math.isclose(single_day, daily * 2, abs_tol=0.01):
            errors.append("max_single_day_charge_usd must equal 2x average_daily_usd")
        if monthly is None or not math.isclose(monthly, daily * 30.4, abs_tol=0.01):
            errors.append("monthly_charge_limit_usd must equal 30.4x average_daily_usd")
    for field, reviewed_value in REVIEWED_BUDGET.items():
        value = _number(budget.get(field))
        if value is None or not math.isclose(value, reviewed_value, abs_tol=0.01):
            errors.append(f"budget.{field} must equal reviewed value {reviewed_value:g}")

    bidding = _mapping(campaign.get("bidding"))
    if bidding.get("strategy") != "MAXIMIZE_CLICKS":
        errors.append("bidding.strategy must equal MAXIMIZE_CLICKS for the initial draft")
    max_cpc = _number(bidding.get("max_cpc_usd"))
    if max_cpc is None or max_cpc <= 0:
        errors.append("bidding.max_cpc_usd must be a positive number")
    elif not math.isclose(max_cpc, REVIEWED_MAX_CPC_USD, abs_tol=0.01):
        errors.append(f"bidding.max_cpc_usd must equal reviewed value {REVIEWED_MAX_CPC_USD:g}")

    networks = _mapping(campaign.get("networks"))
    if networks.get("google_search") is not True:
        errors.append("Google Search must remain enabled")
    if networks.get("search_partners") is not False:
        errors.append("Search Partners must remain disabled for the initial campaign")
    if networks.get("display") is not False:
        errors.append("Display expansion must remain disabled")

    geo = _mapping(campaign.get("geo"))
    if geo.get("type") != "RADIUS":
        errors.append("geo.type must equal RADIUS")
    radius_miles = _number(geo.get("radius_miles"))
    if radius_miles is None or radius_miles < 1:
        errors.append("housing radius must be at least 1 mile")
    elif not math.isclose(radius_miles, REVIEWED_RADIUS_MILES, abs_tol=0.01):
        errors.append(f"geo.radius_miles must equal reviewed value {REVIEWED_RADIUS_MILES:g}")
    if geo.get("presence_only") is not True:
        errors.append("geo.presence_only must remain true")
    if _list(geo.get("postal_codes")):
        errors.append("postal-code targeting is prohibited for this housing campaign")

    policy = _mapping(campaign.get("housing_policy"))
    if policy.get("acknowledgement_required") is not True:
        errors.append("housing-policy acknowledgement must remain required")
    if policy.get("acknowledged") is not False:
        errors.append("housing-policy acknowledgement is an operator gate and must remain false")
    for field, message in (
        ("age_enabled_all", "all age groups must remain enabled"),
        ("gender_enabled_all", "all gender groups must remain enabled"),
        ("parental_status_enabled_all", "all parental-status groups must remain enabled"),
    ):
        if policy.get(field) is not True:
            errors.append(message)
    if policy.get("marital_status_targeting") is not False:
        errors.append("marital-status targeting must remain disabled")
    if policy.get("postal_code_targeting") is not False:
        errors.append("postal-code targeting must remain disabled")
    if _list(policy.get("audience_targeting")):
        errors.append("audience targeting must remain empty for the initial campaign")

    tracking = _mapping(campaign.get("tracking"))
    if tracking.get("utm_source") != "google":
        errors.append("tracking.utm_source must equal google")
    if tracking.get("utm_medium") != "cpc":
        errors.append("tracking.utm_medium must equal cpc")
    if tracking.get("utm_campaign") != "tho_search_high_intent_huffman":
        errors.append("tracking.utm_campaign must use the reviewed campaign slug")
    if tracking.get("utm_content") != "{creative}":
        errors.append("tracking.utm_content must equal {creative}")
    if tracking.get("utm_term") != "{keyword}":
        errors.append("tracking.utm_term must equal {keyword}")

    conversions = {
        item.get("name"): item
        for item in _list(campaign.get("conversions"))
        if isinstance(item, dict) and item.get("name")
    }
    for name in ("generate_lead", "schedule_appointment"):
        conversion = _mapping(conversions.get(name))
        if not conversion:
            errors.append(f"conversion {name} is required")
        elif conversion.get("required_before_enable") is not True:
            errors.append(f"conversion {name} must be required before enable")

    if len(_list(campaign.get("negative_keywords"))) < 10:
        errors.append("at least 10 campaign negative keywords are required")

    ad_groups = _list(campaign.get("ad_groups"))
    if len(ad_groups) < 2:
        errors.append("at least 2 ad groups are required")
    for index, raw_ad_group in enumerate(ad_groups):
        ad_group = _mapping(raw_ad_group)
        label = str(ad_group.get("name") or f"ad_group[{index}]")
        if ad_group.get("status") != "PAUSED":
            errors.append(f"{label}: status must remain PAUSED")
        keywords = _list(ad_group.get("keywords"))
        if not keywords:
            errors.append(f"{label}: at least one keyword is required")
        for keyword in keywords:
            match_type = _mapping(keyword).get("match_type")
            if match_type not in ALLOWED_POSITIVE_MATCH_TYPES:
                errors.append(f"{label}: only EXACT and PHRASE keywords are allowed")
                break

        ad = _mapping(ad_group.get("responsive_search_ad"))
        if ad.get("status") != "PAUSED":
            errors.append(f"{label}: responsive search ad must remain PAUSED")
        final_url = str(ad.get("final_url") or "")
        parsed = urlparse(final_url)
        if parsed.scheme != "https" or parsed.netloc != CANONICAL_HOST:
            errors.append(f"{label}: final_url must use https://{CANONICAL_HOST}")
        elif parsed.path not in ALLOWED_LANDING_PATHS or parsed.query or parsed.fragment:
            errors.append(f"{label}: final_url must use a reviewed public landing path")
        headlines = _list(ad.get("headlines"))
        descriptions = _list(ad.get("descriptions"))
        if len(headlines) < 3:
            errors.append(f"{label}: responsive search ad requires at least 3 headlines")
        if len(descriptions) < 2:
            errors.append(f"{label}: responsive search ad requires at least 2 descriptions")
        for headline in headlines:
            if not isinstance(headline, str) or not headline.strip() or len(headline) > 30:
                errors.append(f"{label}: every headline must contain 1-30 characters")
                break
        for description in descriptions:
            if not isinstance(description, str) or not description.strip() or len(description) > 90:
                errors.append(f"{label}: every description must contain 1-90 characters")
                break
        for field in ("path1", "path2"):
            path = str(ad.get(field) or "")
            if not path or len(path) > 15:
                errors.append(f"{label}: {field} must contain 1-15 characters")

    stop_loss = _mapping(activation.get("stop_loss"))
    if stop_loss.get("action") != "PAUSE_CAMPAIGN":
        errors.append("stop_loss.action must equal PAUSE_CAMPAIGN")
    if stop_loss.get("decision_logic") != "ANY":
        errors.append("stop_loss.decision_logic must equal ANY")
    for field, reviewed_value in REVIEWED_STOP_LOSS.items():
        value = _number(stop_loss.get(field))
        if value is None or value <= 0:
            errors.append(f"stop_loss.{field} must be a positive number")
        elif not math.isclose(value, reviewed_value, abs_tol=0.01):
            errors.append(f"stop_loss.{field} must equal reviewed value {reviewed_value:g}")

    required_checks = {
        value for value in _list(activation.get("required_checks")) if isinstance(value, str)
    }
    if required_checks != REQUIRED_ACTIVATION_CHECKS:
        errors.append("activation_gate.required_checks must match the reviewed checklist")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    args = parser.parse_args(argv)
    payload = json.loads(args.draft.read_text())
    errors = validate_draft(payload)
    print(json.dumps({"valid": not errors, "spend_enabled": False, "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
