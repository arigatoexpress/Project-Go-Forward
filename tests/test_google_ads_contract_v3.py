import copy
import json
import re
from pathlib import Path

import pytest

from scripts.google_ads_launch_draft import (
    GOOGLE_ADS_API_VERSION,
    build_mutate_operations,
    build_mutate_request,
    canonical_contract_json,
    contract_sha256,
    validate_draft,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "google_ads_launch_draft.json"
CUSTOMER_ID = "1234567890"
REVIEWED_CONTRACT_SHA256 = "bade48b68441be6dad21c71276f875288ad4d8bcb9579272f4b2ec4119320893"  # pragma: allowlist secret - public contract digest


def _contract():
    return json.loads(CONTRACT_PATH.read_text())


def _creates(operations, operation_name):
    return [
        operation[operation_name]["create"]
        for operation in operations
        if operation_name in operation
    ]


def _resource_names(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _resource_names(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _resource_names(nested)
    elif isinstance(value, str) and value.startswith("customers/"):
        yield value


def test_checked_in_contract_is_schema_v3_with_separate_readiness_classes():
    contract = _contract()

    assert validate_draft(contract) == []
    assert contract["schema_version"] == 3
    assert contract["deployment"] == {
        "key": "tho-search-high-intent-huffman-v1",
        "feature_flag": "GOOGLE_ADS_ONE_CLICK_ENABLED",
        "api_version": "v25",
        "auto_enable_after_policy_approval": False,
        "contract_hash_algorithm": "sha256",
    }
    assert "search_console_sitemap_accepted" not in contract["readiness"]["hard_checks"]
    assert "search_console_sitemap_accepted" in contract["readiness"]["advisory_checks"]


def test_checked_in_ad_copy_avoids_unproved_inventory_freshness_claims():
    """A stale/fallback catalog must never be advertised as current or available."""
    contract = _contract()
    local_inventory = next(
        group for group in contract["campaign"]["ad_groups"] if group["name"] == "Local Inventory"
    )
    ad = local_inventory["responsive_search_ad"]
    serialized_copy = " ".join([*ad["headlines"], *ad["descriptions"]]).casefold()

    for unsupported_claim in (
        "current",
        "new inventory",
        "available homes",
        "in stock",
        "in-stock",
        "on lot",
        "on-lot",
    ):
        assert unsupported_claim not in serialized_copy


def test_schema_v3_rejects_removed_or_reclassified_hard_checks():
    contract = _contract()
    moved = contract["readiness"]["hard_checks"].pop()
    contract["readiness"]["advisory_checks"].append(moved)

    errors = validate_draft(contract)

    assert "readiness.hard_checks must match the reviewed hard-check list" in errors
    assert "readiness.advisory_checks must match the reviewed advisory-check list" in errors


def test_canonical_json_and_sha256_are_stable_across_key_order_and_whitespace():
    contract = _contract()
    reordered = json.loads(json.dumps(contract, indent=8, sort_keys=True))

    canonical = canonical_contract_json(contract)

    assert canonical == canonical_contract_json(reordered)
    assert canonical == json.dumps(
        json.loads(canonical), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    assert contract_sha256(contract) == contract_sha256(reordered)
    assert contract_sha256(contract) == REVIEWED_CONTRACT_SHA256
    assert re.fullmatch(r"[0-9a-f]{64}", contract_sha256(contract))


def test_contract_hash_changes_when_a_reviewed_cap_changes():
    contract = _contract()
    changed = copy.deepcopy(contract)
    changed["campaign"]["budget"]["average_daily_usd"] = 21

    assert contract_sha256(contract) != contract_sha256(changed)


@pytest.mark.parametrize(
    ("field_path", "value", "expected_error"),
    [
        (
            ("campaign", "budget", "average_daily_usd"),
            20.001,
            "budget.average_daily_usd must equal reviewed value 20",
        ),
        (
            ("campaign", "budget", "max_single_day_charge_usd"),
            40.001,
            "budget.max_single_day_charge_usd must equal reviewed value 40",
        ),
        (
            ("campaign", "budget", "monthly_charge_limit_usd"),
            608.001,
            "budget.monthly_charge_limit_usd must equal reviewed value 608",
        ),
        (
            ("campaign", "bidding", "max_cpc_usd"),
            5.001,
            "bidding.max_cpc_usd must equal reviewed value 5",
        ),
    ],
)
def test_reviewed_money_caps_reject_subcent_drift(field_path, value, expected_error):
    contract = _contract()
    target = contract
    for segment in field_path[:-1]:
        target = target[segment]
    target[field_path[-1]] = value

    assert expected_error in validate_draft(contract)


def test_builder_pins_v25_and_exact_budget_and_max_cpc_micros():
    contract = _contract()
    operations = build_mutate_operations(contract, CUSTOMER_ID)

    assert GOOGLE_ADS_API_VERSION == "v25"
    assert contract["campaign"]["budget"] == {
        "average_daily_usd": 20,
        "max_single_day_charge_usd": 40,
        "monthly_charge_limit_usd": 608,
    }
    budget = _creates(operations, "campaignBudgetOperation")[0]
    campaign = _creates(operations, "campaignOperation")[0]
    assert budget["amountMicros"] == 20_000_000
    assert campaign["targetSpend"] == {"cpcBidCeilingMicros": 5_000_000}
    assert "targetSpendMicros" not in json.dumps(operations)


def test_atomic_graph_uses_unique_ordered_negative_temporary_ids():
    operations = build_mutate_operations(_contract(), CUSTOMER_ID)
    defined_ids = []
    seen = set()

    for operation in operations:
        _operation_name, body = next(iter(operation.items()))
        create = body["create"]
        references = [
            -int(value)
            for resource_name in _resource_names(create)
            for value in re.findall(r"-(\d+)", resource_name)
        ]
        resource_name = create.get("resourceName")
        if resource_name:
            new_id = -int(re.findall(r"-(\d+)", resource_name)[-1])
            references.remove(new_id)
            assert new_id not in seen
            seen.add(new_id)
            defined_ids.append(new_id)
        assert set(references) <= seen

    expected_definitions = 3 + len(_contract()["campaign"]["ad_groups"])
    assert defined_ids == list(range(-1, -expected_definitions - 1, -1))


def test_atomic_graph_is_dependency_ordered_and_grouped_by_resource_type():
    operation_names = [
        next(iter(operation)) for operation in build_mutate_operations(_contract(), CUSTOMER_ID)
    ]

    assert operation_names[:4] == [
        "campaignBudgetOperation",
        "labelOperation",
        "campaignOperation",
        "campaignLabelOperation",
    ]
    assert operation_names[4:] == sorted(
        operation_names[4:],
        key={
            "campaignCriterionOperation": 0,
            "adGroupOperation": 1,
            "adGroupCriterionOperation": 2,
            "adGroupAdOperation": 3,
        }.get,
    )


def test_every_campaign_ad_group_and_ad_is_paused():
    operations = build_mutate_operations(_contract(), CUSTOMER_ID)

    assert {item["status"] for item in _creates(operations, "campaignOperation")} == {"PAUSED"}
    assert {item["status"] for item in _creates(operations, "adGroupOperation")} == {"PAUSED"}
    assert {item["status"] for item in _creates(operations, "adGroupAdOperation")} == {"PAUSED"}
    assert "ENABLED" not in json.dumps(operations)


def test_network_mapping_disables_every_non_google_search_network():
    operations = build_mutate_operations(_contract(), CUSTOMER_ID)
    settings = _creates(operations, "campaignOperation")[0]["networkSettings"]

    assert settings == {
        "targetGoogleSearch": True,
        "targetSearchNetwork": False,
        "targetContentNetwork": False,
        "targetPartnerSearchNetwork": False,
        "targetYouTube": False,
        "targetGoogleTvNetwork": False,
    }


def test_radius_uses_reviewed_coordinates_and_fifty_miles():
    operations = build_mutate_operations(_contract(), CUSTOMER_ID)
    criteria = _creates(operations, "campaignCriterionOperation")
    radius = next(item for item in criteria if "proximity" in item)

    assert radius["proximity"] == {
        "geoPoint": {
            "latitudeInMicroDegrees": 30_018_056,
            "longitudeInMicroDegrees": -95_115_729,
        },
        "radius": 50,
        "radiusUnits": "MILES",
    }


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda value: value["campaign"]["geo"].update(postal_codes=["77336"]),
            "postal-code targeting",
        ),
        (
            lambda value: value["campaign"]["housing_policy"].update(age_enabled_all=False),
            "all age groups",
        ),
        (
            lambda value: value["campaign"]["housing_policy"].update(
                demographic_exclusions=["FEMALE"]
            ),
            "demographic exclusions",
        ),
        (
            lambda value: value["campaign"]["housing_policy"].update(customer_match_targeting=True),
            "Customer Match",
        ),
    ],
)
def test_housing_zip_demographic_and_customer_match_targeting_are_rejected(mutate, expected_error):
    contract = _contract()
    mutate(contract)

    with pytest.raises(ValueError, match=expected_error):
        build_mutate_operations(contract, CUSTOMER_ID)


def test_operation_graph_contains_no_restricted_housing_audience_constructs():
    serialized = json.dumps(build_mutate_operations(_contract(), CUSTOMER_ID)).lower()

    for forbidden in (
        "postalcode",
        "customer_match",
        "customermatch",
        "agerange",
        "gender",
        "parental",
    ):
        assert forbidden not in serialized


def test_validate_and_create_requests_share_identical_operations_and_fail_atomically():
    contract = _contract()

    validation = build_mutate_request(contract, CUSTOMER_ID, validate_only=True)
    creation = build_mutate_request(contract, CUSTOMER_ID, validate_only=False)

    assert validation["mutateOperations"] == creation["mutateOperations"]
    assert validation == {
        **creation,
        "validateOnly": True,
    }
    assert creation["validateOnly"] is False
    assert validation["partialFailure"] is False
    assert validation["responseContentType"] == "RESOURCE_NAME_ONLY"


def test_builder_does_not_mutate_the_contract():
    contract = _contract()
    original = copy.deepcopy(contract)

    build_mutate_request(contract, CUSTOMER_ID, validate_only=True)

    assert contract == original
