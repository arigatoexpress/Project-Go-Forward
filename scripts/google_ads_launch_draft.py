#!/usr/bin/env python3
"""Pure contract and operation builder for the zero-spend Google Ads draft.

This command never imports the Google Ads client, reads credentials, or makes a
network request. It validates the reviewed contract and can build inert v25
``GoogleAdsService.Mutate`` request bodies for later validate-only or paused
creation flows. It never sends those bodies or enables an Ads resource.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_DRAFT = Path(__file__).resolve().parents[1] / "config" / "google_ads_launch_draft.json"
GOOGLE_ADS_API_VERSION = "v25"
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
REVIEWED_LATITUDE = 30.018056
REVIEWED_LONGITUDE = -95.115729
REVIEWED_STOP_LOSS = {
    "evaluation_window_days": 7.0,
    "zero_reachable_leads_spend_usd": 200.0,
    "zero_reachable_leads_clicks": 100.0,
    "max_reachable_lead_cpa_usd": 150.0,
    "minimum_reachable_leads_for_cpa_rule": 3.0,
}
REQUIRED_HARD_CHECKS = {
    "feature_flag_enabled",
    "draft_validator_green",
    "dedicated_job_runtime_green",
    "google_ads_account_access_green",
    "billing_and_account_serving_eligible",
    "housing_policy_acknowledged_in_google_ads",
    "landing_pages_live_canonical_and_lead_capable",
    "ga4_or_gtm_exactly_one_loader",
    "generate_lead_single_fire_verified",
    "schedule_appointment_single_fire_verified",
    "google_ads_conversion_import_verified",
    "no_duplicate_active_deployment",
    "stop_loss_scheduler_green",
    "budget_and_stop_loss_bound_to_passkey_approval",
}
REQUIRED_ADVISORY_CHECKS = {
    "search_console_sitemap_accepted",
    "business_profile_link_verified",
    "business_profile_performance_api_ready",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _matches_reviewed_number(value: Any, expected: int | float) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        candidate = Decimal(str(value))
    except InvalidOperation:
        return False
    return candidate.is_finite() and candidate == Decimal(str(expected))


def validate_draft(payload: dict[str, Any]) -> list[str]:
    """Return deterministic validation errors without mutating ``payload``."""
    errors: list[str] = []
    campaign = _mapping(payload.get("campaign"))
    activation = _mapping(payload.get("activation_gate"))

    if payload.get("schema_version") != 3:
        errors.append("schema_version must equal 3")
    if payload.get("mode") != "VALIDATE_ONLY":
        errors.append("mode must remain VALIDATE_ONLY")

    deployment = _mapping(payload.get("deployment"))
    expected_deployment = {
        "key": "tho-search-high-intent-huffman-v1",
        "feature_flag": "GOOGLE_ADS_ONE_CLICK_ENABLED",
        "api_version": GOOGLE_ADS_API_VERSION,
        "auto_enable_after_policy_approval": False,
        "contract_hash_algorithm": "sha256",
    }
    for field, expected in expected_deployment.items():
        if deployment.get(field) != expected:
            if isinstance(expected, str):
                errors.append(f"deployment.{field} must equal {expected}")
            else:
                errors.append(f"deployment.{field} must remain {str(expected).lower()}")

    readiness = _mapping(payload.get("readiness"))
    hard_checks = {value for value in _list(readiness.get("hard_checks")) if isinstance(value, str)}
    advisory_checks = {
        value for value in _list(readiness.get("advisory_checks")) if isinstance(value, str)
    }
    if hard_checks != REQUIRED_HARD_CHECKS:
        errors.append("readiness.hard_checks must match the reviewed hard-check list")
    if advisory_checks != REQUIRED_ADVISORY_CHECKS:
        errors.append("readiness.advisory_checks must match the reviewed advisory-check list")

    control_plane = _mapping(payload.get("control_plane"))
    expected_control_plane = {
        "runtime": "DEDICATED_CLOUD_RUN_JOB",
        "service_account_id": "google-growth-control",
        "authentication": "APPLICATION_DEFAULT_CREDENTIALS",
        "persistent_service_account_key": False,
        "developer_token_source": "SECRET_MANAGER",
        "customer_id_source": "SECRET_MANAGER",
        "live_probe_required": True,
    }
    for field, expected in expected_control_plane.items():
        if control_plane.get(field) != expected:
            if isinstance(expected, str):
                errors.append(f"control_plane.{field} must equal {expected}")
            else:
                errors.append(f"control_plane.{field} must remain {str(expected).lower()}")
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
        if value is None or not _matches_reviewed_number(value, reviewed_value):
            errors.append(f"budget.{field} must equal reviewed value {reviewed_value:g}")

    bidding = _mapping(campaign.get("bidding"))
    if bidding.get("strategy") != "MAXIMIZE_CLICKS":
        errors.append("bidding.strategy must equal MAXIMIZE_CLICKS for the initial draft")
    max_cpc = _number(bidding.get("max_cpc_usd"))
    if max_cpc is None or max_cpc <= 0:
        errors.append("bidding.max_cpc_usd must be a positive number")
    elif not _matches_reviewed_number(max_cpc, REVIEWED_MAX_CPC_USD):
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
    center = _mapping(geo.get("center"))
    latitude = _number(center.get("latitude"))
    longitude = _number(center.get("longitude"))
    if latitude is None or not math.isclose(latitude, REVIEWED_LATITUDE, abs_tol=0.000001):
        errors.append(f"geo.center.latitude must equal reviewed value {REVIEWED_LATITUDE:g}")
    if longitude is None or not math.isclose(longitude, REVIEWED_LONGITUDE, abs_tol=0.000001):
        errors.append(f"geo.center.longitude must equal reviewed value {REVIEWED_LONGITUDE:g}")
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
    if _list(policy.get("demographic_exclusions")):
        errors.append("demographic exclusions are prohibited for this housing campaign")
    if policy.get("customer_match_targeting") is not False:
        errors.append("Customer Match targeting must remain disabled")
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

    return errors


def canonical_contract_json(payload: dict[str, Any]) -> str:
    """Return deterministic UTF-8 JSON bytes-as-text for contract hashing.

    The contract intentionally contains only ordinary JSON values. Sorting
    object keys and removing insignificant whitespace makes the digest
    independent of source formatting and dictionary insertion order.
    """
    if not isinstance(payload, dict):
        raise TypeError("Google Ads contract must be a JSON object")
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def contract_sha256(payload: dict[str, Any]) -> str:
    """Return the lowercase SHA-256 hex digest of the canonical contract."""
    canonical = canonical_contract_json(payload).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_customer_id(value: str) -> str:
    normalized = str(value).strip().replace("-", "")
    if not re.fullmatch(r"\d{10}", normalized):
        raise ValueError("Google Ads customer ID must contain exactly 10 digits")
    return normalized


def _micros(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a decimal number")
    try:
        micros = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    integral = micros.to_integral_value()
    if micros != integral:
        raise ValueError(f"{field} must resolve to whole micros")
    return int(integral)


def _microdegrees(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a decimal number")
    try:
        microdegrees = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    integral = microdegrees.to_integral_value()
    if microdegrees != integral:
        raise ValueError(f"{field} must resolve to whole microdegrees")
    return int(integral)


def build_mutate_operations(payload: dict[str, Any], customer_id: str) -> list[dict[str, Any]]:
    """Build one pure, dependency-ordered, paused-only v25 operation graph.

    This function performs no I/O. The returned dictionaries match the REST
    shape accepted by ``GoogleAdsService.Mutate`` but are never submitted here.
    """
    errors = validate_draft(payload)
    if errors:
        raise ValueError("Invalid Google Ads contract: " + "; ".join(errors))

    customer = _normalize_customer_id(customer_id)
    campaign = _mapping(payload.get("campaign"))
    budget = _mapping(campaign.get("budget"))
    bidding = _mapping(campaign.get("bidding"))
    geo = _mapping(campaign.get("geo"))
    center = _mapping(geo.get("center"))
    tracking = _mapping(campaign.get("tracking"))
    ad_groups = [_mapping(value) for value in _list(campaign.get("ad_groups"))]

    next_temporary_id = -1

    def allocate_temporary_id() -> int:
        nonlocal next_temporary_id
        allocated = next_temporary_id
        next_temporary_id -= 1
        return allocated

    operations: list[dict[str, Any]] = []

    budget_id = allocate_temporary_id()
    budget_resource = f"customers/{customer}/campaignBudgets/{budget_id}"
    operations.append(
        {
            "campaignBudgetOperation": {
                "create": {
                    "resourceName": budget_resource,
                    "name": f"{campaign['name']} | Budget",
                    "amountMicros": _micros(
                        budget.get("average_daily_usd"), field="budget.average_daily_usd"
                    ),
                    "deliveryMethod": "STANDARD",
                    "explicitlyShared": False,
                }
            }
        }
    )

    label_id = allocate_temporary_id()
    label_resource = f"customers/{customer}/labels/{label_id}"
    digest = contract_sha256(payload)
    operations.append(
        {
            "labelOperation": {
                "create": {
                    "resourceName": label_resource,
                    "name": f"tho-contract-{digest[:12]}",
                    "description": "Texas Home Outlet immutable campaign contract",
                }
            }
        }
    )

    campaign_id = allocate_temporary_id()
    campaign_resource = f"customers/{customer}/campaigns/{campaign_id}"
    final_url_suffix = "&".join(
        f"{key}={tracking[key]}"
        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
    )
    operations.append(
        {
            "campaignOperation": {
                "create": {
                    "resourceName": campaign_resource,
                    "name": campaign["name"],
                    "status": "PAUSED",
                    "advertisingChannelType": "SEARCH",
                    "campaignBudget": budget_resource,
                    "targetSpend": {
                        "cpcBidCeilingMicros": _micros(
                            bidding.get("max_cpc_usd"), field="bidding.max_cpc_usd"
                        )
                    },
                    "networkSettings": {
                        "targetGoogleSearch": True,
                        "targetSearchNetwork": False,
                        "targetContentNetwork": False,
                        "targetPartnerSearchNetwork": False,
                        "targetYouTube": False,
                        "targetGoogleTvNetwork": False,
                    },
                    "geoTargetTypeSetting": {
                        "positiveGeoTargetType": "PRESENCE",
                        "negativeGeoTargetType": "PRESENCE",
                    },
                    "containsEuPoliticalAdvertising": ("DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"),
                    "finalUrlSuffix": final_url_suffix,
                }
            }
        }
    )

    operations.append(
        {
            "campaignLabelOperation": {
                "create": {
                    "campaign": campaign_resource,
                    "label": label_resource,
                }
            }
        }
    )

    campaign_criterion_operations: list[dict[str, Any]] = []
    campaign_criterion_operations.append(
        {
            "campaignCriterionOperation": {
                "create": {
                    "campaign": campaign_resource,
                    "status": "PAUSED",
                    "negative": False,
                    "proximity": {
                        "geoPoint": {
                            "latitudeInMicroDegrees": _microdegrees(
                                center.get("latitude"), field="geo.center.latitude"
                            ),
                            "longitudeInMicroDegrees": _microdegrees(
                                center.get("longitude"), field="geo.center.longitude"
                            ),
                        },
                        "radius": int(REVIEWED_RADIUS_MILES),
                        "radiusUnits": "MILES",
                    },
                }
            }
        }
    )
    for raw_keyword in _list(campaign.get("negative_keywords")):
        keyword = _mapping(raw_keyword)
        campaign_criterion_operations.append(
            {
                "campaignCriterionOperation": {
                    "create": {
                        "campaign": campaign_resource,
                        "status": "PAUSED",
                        "negative": True,
                        "keyword": {
                            "text": keyword["text"],
                            "matchType": keyword["match_type"],
                        },
                    }
                }
            }
        )
    operations.extend(campaign_criterion_operations)

    ad_group_operations: list[dict[str, Any]] = []
    ad_group_resources: list[tuple[dict[str, Any], str]] = []
    for ad_group in ad_groups:
        ad_group_id = allocate_temporary_id()
        ad_group_resource = f"customers/{customer}/adGroups/{ad_group_id}"
        ad_group_resources.append((ad_group, ad_group_resource))
        ad_group_operations.append(
            {
                "adGroupOperation": {
                    "create": {
                        "resourceName": ad_group_resource,
                        "name": ad_group["name"],
                        "campaign": campaign_resource,
                        "status": "PAUSED",
                        "type": "SEARCH_STANDARD",
                    }
                }
            }
        )
    operations.extend(ad_group_operations)

    ad_group_criterion_operations: list[dict[str, Any]] = []
    for ad_group, ad_group_resource in ad_group_resources:
        for raw_keyword in _list(ad_group.get("keywords")):
            keyword = _mapping(raw_keyword)
            ad_group_criterion_operations.append(
                {
                    "adGroupCriterionOperation": {
                        "create": {
                            "adGroup": ad_group_resource,
                            "status": "PAUSED",
                            "keyword": {
                                "text": keyword["text"],
                                "matchType": keyword["match_type"],
                            },
                        }
                    }
                }
            )
    operations.extend(ad_group_criterion_operations)

    ad_group_ad_operations: list[dict[str, Any]] = []
    for ad_group, ad_group_resource in ad_group_resources:
        ad = _mapping(ad_group.get("responsive_search_ad"))
        ad_group_ad_operations.append(
            {
                "adGroupAdOperation": {
                    "create": {
                        "adGroup": ad_group_resource,
                        "status": "PAUSED",
                        "ad": {
                            "finalUrls": [ad["final_url"]],
                            "responsiveSearchAd": {
                                "headlines": [{"text": value} for value in ad["headlines"]],
                                "descriptions": [{"text": value} for value in ad["descriptions"]],
                                "path1": ad["path1"],
                                "path2": ad["path2"],
                            },
                        },
                    }
                }
            }
        )
    operations.extend(ad_group_ad_operations)
    return operations


def build_mutate_request(
    payload: dict[str, Any], customer_id: str, *, validate_only: bool
) -> dict[str, Any]:
    """Return an inert atomic mutate body; callers choose validation vs create."""
    if not isinstance(validate_only, bool):
        raise TypeError("validate_only must be a boolean")
    return {
        "mutateOperations": build_mutate_operations(payload, customer_id),
        "partialFailure": False,
        "validateOnly": validate_only,
        "responseContentType": "RESOURCE_NAME_ONLY",
    }


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
