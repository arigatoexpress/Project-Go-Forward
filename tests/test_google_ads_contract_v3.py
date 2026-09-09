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
REVIEWED_CONTRACT_SHA256 = "9a55b1e3b396efd383995642968c11c15d1d6af473a2a4e988a1ae0a08590192"  # pragma: allowlist secret - public contract digest


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
        "api_version": "v25",
        "contract_hash_algorithm": "sha256",
        "approval_control": {
            "required_true_envs": [
                "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED",
                "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED",
                "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED",
            ],
            "revision_binding": {
                "readiness_env": "THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION",
                "source_env": "APP_VERSION",
                "format": "LOWERCASE_GIT_SHA40",
                "must_match": True,
            },
            "fixed_target_envs": {
                "project": "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT",
                "region": "THO_GOOGLE_ADS_PAUSED_CREATE_REGION",
                "job": "THO_GOOGLE_ADS_PAUSED_CREATE_JOB",
            },
            "requires_authority_state": "SERVER_VALIDATED",
            "requires_authority_version": 2,
            "requires_fresh_evidence": "google_ads_account_access_and_usd_green",
            "binds_checked_in_contract_and_caps": True,
            "owner_authentication": {
                "session": "OWNER_PASSKEY_COOKIE",
                "csrf": "COOKIE_HEADER_MATCH",
                "step_up": "WEBAUTHN_UV_REQUIRED",
            },
            "owner_allowlist": {
                "ads_env": "THO_GOOGLE_ADS_OWNER_EMAILS",
                "passkey_envs": [
                    "THO_PASSKEY_OWNER_EMAILS",
                    "THO_ADMIN_OWNER_EMAILS",
                ],
                "must_be_nonempty": True,
                "must_equal_effective_passkey_owner_allowlist": True,
            },
            "consumes_single_use_owner_proof": True,
            "writes_control_plane_state_only": True,
            "invokes_job": False,
        },
        "dispatch_control": {
            "required_true_envs": ["THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED"],
            "requires_approval_control": True,
            "requires_approved_outbox": True,
            "invokes_paused_create_only": True,
            "request_overrides_allowed": False,
            "invocation_acceptance_is_completion": False,
        },
        "activation_supported": False,
        "spend_authorized": False,
    }
    assert "GOOGLE_ADS_ONE_CLICK_ENABLED" not in json.dumps(contract)
    assert "feature_flag_enabled" not in contract["readiness"]["hard_checks"]
    assert (
        "paused_create_approval_and_dispatch_controls_verified"
        in contract["readiness"]["hard_checks"]
    )
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


@pytest.mark.parametrize(
    ("path", "value", "expected_error"),
    [
        (
            ("approval_control", "required_true_envs"),
            ["GOOGLE_ADS_ONE_CLICK_ENABLED"],
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("approval_control", "invokes_job"),
            True,
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("approval_control", "consumes_single_use_owner_proof"),
            False,
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("approval_control", "requires_fresh_evidence"),
            "google_ads_account_access_green",
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("approval_control", "owner_authentication", "csrf"),
            "NONE",
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            (
                "approval_control",
                "owner_allowlist",
                "must_equal_effective_passkey_owner_allowlist",
            ),
            False,
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("approval_control", "revision_binding"),
            {"must_match": False},
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("approval_control", "fixed_target_envs", "job"),
            "UNREVIEWED_JOB",
            "deployment.approval_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("dispatch_control", "required_true_envs"),
            ["UNREVIEWED_DISPATCH"],
            "deployment.dispatch_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("dispatch_control", "requires_approval_control"),
            False,
            "deployment.dispatch_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("dispatch_control", "invocation_acceptance_is_completion"),
            True,
            "deployment.dispatch_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("dispatch_control", "requires_approved_outbox"),
            False,
            "deployment.dispatch_control must match reviewed PAUSED-create semantics",
        ),
        (
            ("activation_supported",),
            True,
            "deployment.activation_supported must remain false",
        ),
        (
            ("spend_authorized",),
            True,
            "deployment.spend_authorized must remain false",
        ),
    ],
)
def test_contract_rejects_legacy_or_weakened_runtime_control_semantics(path, value, expected_error):
    contract = _contract()
    target = contract["deployment"]
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    assert expected_error in validate_draft(contract)


@pytest.mark.parametrize("obsolete_field", ["feature_flag", "auto_enable_after_policy_approval"])
def test_contract_rejects_obsolete_deployment_control_fields(obsolete_field):
    contract = _contract()
    contract["deployment"][obsolete_field] = "legacy"

    assert f"deployment.{obsolete_field} is obsolete" in validate_draft(contract)


def test_contract_rejects_unreviewed_control_or_conversion_intent_fields():
    contract = _contract()
    contract["deployment"]["alternate_dispatch"] = "unsupported"
    contract["campaign"]["conversion_intent"]["conversion_goal_resource"] = "unsupported"

    errors = validate_draft(contract)

    assert "deployment fields must match the reviewed PAUSED-create contract" in errors
    assert "conversion_intent fields must match the reviewed non-operative intent" in errors


def test_contract_rejects_old_overclaim_about_approval_transaction_effects():
    contract = _contract()
    contract["deployment"]["approval_control"]["writes_approval_and_outbox_only"] = True

    errors = validate_draft(contract)

    assert "deployment.approval_control must match reviewed PAUSED-create semantics" in errors


def test_conversion_intent_is_explicitly_nonoperative_and_a_hard_activation_hold():
    contract = _contract()

    assert "conversions" not in contract["campaign"]
    assert contract["campaign"]["conversion_intent"] == {
        "provider_goal_operations_in_paused_create": False,
        "import_required_before_activation": True,
        "activation_hold_check": "google_ads_conversion_import_verified",
        "events": [
            {"name": "schedule_appointment", "primary": True},
            {"name": "generate_lead", "primary": False},
        ],
    }
    assert (
        contract["campaign"]["conversion_intent"]["activation_hold_check"]
        in contract["readiness"]["hard_checks"]
    )

    serialized_operations = json.dumps(
        build_mutate_operations(contract, CUSTOMER_ID), sort_keys=True
    ).casefold()
    for forbidden in (
        "conversionaction",
        "campaignconversiongoal",
        "conversiongoalcampaignconfig",
    ):
        assert forbidden not in serialized_operations

    runbook = (ROOT / "docs" / "runbooks" / "google-growth-activation.md").read_text()
    assert "attaches no conversion-action or" in runbook
    assert "campaign-conversion-goal operations" in runbook
    assert "hard\n`google_ads_conversion_import_verified` pre-activation hold" in runbook
    assert "cannot activate a campaign or authorize spend" in runbook


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda value: value["campaign"]["conversion_intent"].update(
                provider_goal_operations_in_paused_create=True
            ),
            "conversion_intent.provider_goal_operations_in_paused_create must remain false",
        ),
        (
            lambda value: value["campaign"]["conversion_intent"].update(
                import_required_before_activation=False
            ),
            "conversion_intent.import_required_before_activation must remain true",
        ),
        (
            lambda value: value["campaign"]["conversion_intent"].update(
                activation_hold_check="optional"
            ),
            "conversion_intent.activation_hold_check must equal google_ads_conversion_import_verified",
        ),
        (
            lambda value: value["campaign"]["conversion_intent"]["events"].append(
                {"name": "phone_call", "primary": True}
            ),
            "conversion_intent.events must match the reviewed non-operative intent",
        ),
        (
            lambda value: value["campaign"].update(conversions=[]),
            "campaign.conversions is obsolete; use non-operative conversion_intent",
        ),
    ],
)
def test_conversion_intent_rejects_goal_attachment_or_activation_hold_drift(mutate, expected_error):
    contract = _contract()
    mutate(contract)

    assert expected_error in validate_draft(contract)


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
