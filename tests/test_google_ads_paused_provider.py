from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.google_ads_launch_draft import build_mutate_request, contract_sha256
from scripts.google_ads_paused_provider import (
    ADS_API_VERSION,
    GoogleAdsProviderError,
    GoogleAdsV25PausedProvider,
    ProviderErrorCode,
)
from scripts.google_ads_paused_worker import (
    AmbiguousProviderTimeout,
    DeploymentState,
    DraftReviewControlPlane,
    InMemoryAuthorityLedger,
    PausedCreateApproval,
    PausedCreateControlPlane,
    PausedCreateWorker,
    StaticContractSource,
    contract_label,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "google_ads_launch_draft.json").read_text())
CUSTOMER_ID = "1234567890"
CAMPAIGN_RESOURCE = f"customers/{CUSTOMER_ID}/campaigns/987654321"
BUDGET_RESOURCE = f"customers/{CUSTOMER_ID}/campaignBudgets/111111111"
AD_GROUP_RESOURCES = {
    "Local Inventory": f"customers/{CUSTOMER_ID}/adGroups/222222222",
    "Showroom Tours": f"customers/{CUSTOMER_ID}/adGroups/333333333",
}


class _Credentials:
    token = "fake-access-token"

    def refresh(self, _request):
        return None


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.headers = {"request-id": "raw-request-id-must-not-escape"}

    def json(self):
        return copy.deepcopy(self._payload)


def _campaign_row():
    campaign = CONTRACT["campaign"]
    tracking = campaign["tracking"]
    return {
        "campaign": {
            "resourceName": CAMPAIGN_RESOURCE,
            "name": campaign["name"],
            "status": "PAUSED",
            "advertisingChannelType": "SEARCH",
            "campaignBudget": BUDGET_RESOURCE,
            "targetSpend": {"cpcBidCeilingMicros": "5000000"},
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
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            "finalUrlSuffix": "&".join(
                f"{key}={tracking[key]}"
                for key in (
                    "utm_source",
                    "utm_medium",
                    "utm_campaign",
                    "utm_content",
                    "utm_term",
                )
            ),
        },
        "campaignBudget": {
            "resourceName": BUDGET_RESOURCE,
            "name": f"{campaign['name']} | Budget",
            "amountMicros": "20000000",
            "deliveryMethod": "STANDARD",
            "explicitlyShared": False,
        },
    }


def _campaign_criterion_rows():
    geo = CONTRACT["campaign"]["geo"]
    rows = [
        {
            "campaignCriterion": {
                "campaign": CAMPAIGN_RESOURCE,
                "status": "PAUSED",
                "negative": False,
                "proximity": {
                    "geoPoint": {
                        "latitudeInMicroDegrees": 30_018_056,
                        "longitudeInMicroDegrees": -95_115_729,
                    },
                    "radius": int(geo["radius_miles"]),
                    "radiusUnits": "MILES",
                },
            }
        }
    ]
    rows.extend(
        {
            "campaignCriterion": {
                "campaign": CAMPAIGN_RESOURCE,
                "status": "PAUSED",
                "negative": True,
                "keyword": {
                    "text": keyword["text"],
                    "matchType": keyword["match_type"],
                },
            }
        }
        for keyword in CONTRACT["campaign"]["negative_keywords"]
    )
    return rows


def _ad_group_rows():
    return [
        {
            "adGroup": {
                "resourceName": AD_GROUP_RESOURCES[group["name"]],
                "campaign": CAMPAIGN_RESOURCE,
                "name": group["name"],
                "status": "PAUSED",
                "type": "SEARCH_STANDARD",
            }
        }
        for group in CONTRACT["campaign"]["ad_groups"]
    ]


def _ad_group_criterion_rows():
    rows = []
    for group in CONTRACT["campaign"]["ad_groups"]:
        for keyword in group["keywords"]:
            rows.append(
                {
                    "adGroup": {"resourceName": AD_GROUP_RESOURCES[group["name"]]},
                    "adGroupCriterion": {
                        "adGroup": AD_GROUP_RESOURCES[group["name"]],
                        "status": "PAUSED",
                        "type": "KEYWORD",
                        "negative": False,
                        "keyword": {
                            "text": keyword["text"],
                            "matchType": keyword["match_type"],
                        },
                    },
                }
            )
    return rows


def _ad_group_ad_rows():
    rows = []
    for group in CONTRACT["campaign"]["ad_groups"]:
        ad = group["responsive_search_ad"]
        rows.append(
            {
                "adGroup": {"resourceName": AD_GROUP_RESOURCES[group["name"]]},
                "adGroupAd": {
                    "adGroup": AD_GROUP_RESOURCES[group["name"]],
                    "status": "PAUSED",
                    "ad": {
                        "finalUrls": [ad["final_url"]],
                        "responsiveSearchAd": {
                            "headlines": [
                                {"text": value, "pinnedField": "UNSPECIFIED"}
                                for value in ad["headlines"]
                            ],
                            "descriptions": [
                                {"text": value, "pinnedField": "UNSPECIFIED"}
                                for value in ad["descriptions"]
                            ],
                            "path1": ad["path1"],
                            "path2": ad["path2"],
                        },
                    },
                },
            }
        )
    return rows


def _readback_payload(query: str):
    if "FROM campaign_label" in query:
        return {
            "results": [
                {
                    "campaign": {"resourceName": CAMPAIGN_RESOURCE},
                    "label": {
                        "name": contract_label(CONTRACT),
                        "description": "Texas Home Outlet immutable campaign contract",
                    },
                }
            ]
        }
    if "FROM campaign_criterion" in query:
        return {"results": _campaign_criterion_rows()}
    if "FROM ad_group_criterion" in query:
        return {"results": _ad_group_criterion_rows()}
    if "FROM ad_group_ad" in query:
        return {"results": _ad_group_ad_rows()}
    if "FROM ad_group" in query:
        return {"results": _ad_group_rows()}
    if "FROM campaign" in query:
        return {"results": [_campaign_row()]}
    raise AssertionError("unexpected query")


class _Responder:
    def __init__(self, *, label_found=True):
        self.calls = []
        self.label_found = label_found
        self.mutate_count = 0

    def __call__(self, url, *, headers, json, timeout, allow_redirects):
        self.calls.append(
            (url, copy.deepcopy(headers), copy.deepcopy(json), timeout, allow_redirects)
        )
        if url.endswith(":mutate"):
            self.mutate_count += 1
            return _Response(payload={"mutateOperationResponses": []})
        query = json["query"]
        payload = _readback_payload(query)
        if "FROM campaign_label" in query and not self.label_found:
            payload = {"results": []}
        return _Response(payload=payload)


def _provider(responder):
    return GoogleAdsV25PausedProvider(
        customer_id=CUSTOMER_ID,
        developer_token="fake-developer-token",
        login_customer_id="9999999999",
        contract=CONTRACT,
        credential_loader=lambda **_kwargs: (_Credentials(), None),
        auth_request_factory=lambda: object(),
        requester=responder,
    )


def _approved_ledger():
    source = StaticContractSource(CONTRACT)
    ledger = InMemoryAuthorityLedger()
    draft = DraftReviewControlPlane(ledger, source).ensure_internal_draft()
    validated = DraftReviewControlPlane(ledger, source).server_validate(draft.deployment_id)
    approval = PausedCreateApproval.for_record(validated)
    approved = PausedCreateControlPlane(
        ledger,
        SimpleNamespace(invoke=lambda _deployment_id: None),
    ).approve_paused_create(approval)
    return ledger, source, approved


def test_reviewed_graph_has_exact_caps_label_and_every_serving_resource_paused():
    request = build_mutate_request(CONTRACT, CUSTOMER_ID, validate_only=False)
    creates = {
        operation_name: [
            operation[operation_name]["create"]
            for operation in request["mutateOperations"]
            if operation_name in operation
        ]
        for operation_name in (
            "campaignBudgetOperation",
            "labelOperation",
            "campaignOperation",
            "campaignCriterionOperation",
            "adGroupOperation",
            "adGroupCriterionOperation",
            "adGroupAdOperation",
        )
    }

    assert request["partialFailure"] is False
    assert request["validateOnly"] is False
    assert creates["campaignBudgetOperation"][0]["amountMicros"] == 20_000_000
    assert creates["campaignOperation"][0]["targetSpend"] == {"cpcBidCeilingMicros": 5_000_000}
    assert creates["labelOperation"][0]["name"] == contract_label(CONTRACT)
    for operation_name in (
        "campaignOperation",
        "campaignCriterionOperation",
        "adGroupOperation",
        "adGroupCriterionOperation",
        "adGroupAdOperation",
    ):
        assert creates[operation_name]
        assert {create["status"] for create in creates[operation_name]} == {"PAUSED"}


def test_v25_rest_provider_runs_validate_only_then_atomic_paused_create_and_full_readback():
    responder = _Responder(label_found=False)
    provider = _provider(responder)
    ledger, source, approved = _approved_ledger()

    find_calls = 0
    original_find = provider.find_by_contract_label

    def staged_find(label):
        nonlocal find_calls
        find_calls += 1
        responder.label_found = find_calls > 1
        return original_find(label)

    provider.find_by_contract_label = staged_find
    worker = PausedCreateWorker(
        ledger,
        source,
        lambda contract, *, validate_only: build_mutate_request(
            contract,
            CUSTOMER_ID,
            validate_only=validate_only,
        ),
        provider,
    )

    result = worker.run(approved.deployment_id)

    mutate_calls = [call for call in responder.calls if call[0].endswith(":mutate")]
    assert [call[2]["validateOnly"] for call in mutate_calls] == [True, False]
    assert all(call[2]["partialFailure"] is False for call in mutate_calls)
    assert all(f"/{ADS_API_VERSION}/" in call[0] for call in responder.calls)
    assert all(call[4] is False for call in responder.calls)
    assert result.state is DeploymentState.PAUSED_CREATED
    assert result.reconciled is False
    assert responder.mutate_count == 2
    assert "ENABLED" not in json.dumps([call[2] for call in mutate_calls])


def test_readback_matches_every_reviewed_resource_and_returns_only_hashable_boundary_value():
    responder = _Responder()
    deployment = _provider(responder).find_by_contract_label(contract_label(CONTRACT))

    assert deployment.contract_hash == f"sha256:{contract_sha256(CONTRACT)}"
    assert deployment.status == "PAUSED"
    assert CAMPAIGN_RESOURCE not in repr(deployment)
    search_calls = [call for call in responder.calls if call[0].endswith("googleAds:search")]
    assert len(search_calls) == 6
    assert all(call[2].get("pageSize") == 1000 for call in search_calls)
    assert all(
        set(call[1])
        == {
            "Authorization",
            "Content-Type",
            "developer-token",
            "login-customer-id",
        }
        for call in search_calls
    )


def test_readback_accepts_proto_json_omitted_false_values_and_integral_radius():
    class ProtoJsonResponder(_Responder):
        def __call__(self, url, *, headers, json, timeout, allow_redirects):
            response = super().__call__(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            query = json.get("query", "")
            if "FROM campaign " in query:
                campaign = response._payload["results"][0]["campaign"]
                for key in (
                    "targetSearchNetwork",
                    "targetContentNetwork",
                    "targetPartnerSearchNetwork",
                    "targetYouTube",
                    "targetGoogleTvNetwork",
                ):
                    campaign["networkSettings"].pop(key)
                response._payload["results"][0]["campaignBudget"].pop("explicitlyShared")
            elif "FROM campaign_criterion" in query:
                criterion = response._payload["results"][0]["campaignCriterion"]
                criterion.pop("negative")
                criterion["proximity"]["radius"] = 50.0
            elif "FROM ad_group_criterion" in query:
                response._payload["results"][0]["adGroupCriterion"].pop("negative")
            elif "FROM ad_group_ad" in query:
                responsive = response._payload["results"][0]["adGroupAd"]["ad"][
                    "responsiveSearchAd"
                ]
                for asset in [*responsive["headlines"], *responsive["descriptions"]]:
                    asset.pop("pinnedField")
            return response

    deployment = _provider(ProtoJsonResponder()).find_by_contract_label(contract_label(CONTRACT))

    assert deployment.status == "PAUSED"


@pytest.mark.parametrize(
    "mutates_payload",
    [
        lambda payload: payload["results"][0]["campaign"].update(status="ENABLED"),
        lambda payload: payload["results"][0]["campaignBudget"].update(amountMicros="20000001"),
        lambda payload: payload["results"][0]["campaign"]["targetSpend"].update(
            cpcBidCeilingMicros="5000001"
        ),
    ],
)
def test_campaign_status_budget_or_bid_drift_fails_sanitized(mutates_payload):
    class DriftResponder(_Responder):
        def __call__(self, url, *, headers, json, timeout, allow_redirects):
            response = super().__call__(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if "FROM campaign " in json.get("query", ""):
                mutates_payload(response._payload)
            return response

    with pytest.raises(GoogleAdsProviderError) as exc_info:
        _provider(DriftResponder()).find_by_contract_label(contract_label(CONTRACT))

    assert exc_info.value.code is ProviderErrorCode.READBACK_MISMATCH
    assert exc_info.value.request_hash.startswith("sha256:")
    assert CUSTOMER_ID not in str(exc_info.value)
    assert "raw-request-id" not in str(exc_info.value)


def test_ad_group_keyword_ad_content_or_extra_resource_drift_fails_closed():
    mutations = (
        (
            "FROM ad_group ",
            lambda payload: payload["results"][0]["adGroup"].update(status="ENABLED"),
        ),
        (
            "FROM ad_group_criterion",
            lambda payload: payload["results"][0]["adGroupCriterion"]["keyword"].update(
                text="drifted"
            ),
        ),
        (
            "FROM ad_group_criterion",
            lambda payload: payload["results"][0]["adGroupCriterion"].update(negative=True),
        ),
        (
            "FROM ad_group_criterion",
            lambda payload: payload["results"][0]["adGroupCriterion"].update(type="AUDIENCE"),
        ),
        (
            "FROM ad_group_ad",
            lambda payload: payload["results"][0]["adGroupAd"]["ad"]["responsiveSearchAd"][
                "headlines"
            ].append({"text": "Unexpected"}),
        ),
        (
            "FROM ad_group_ad",
            lambda payload: payload["results"][0]["adGroupAd"]["ad"]["responsiveSearchAd"][
                "headlines"
            ][0].update(pinnedField="HEADLINE_1"),
        ),
        (
            "FROM ad_group_ad",
            lambda payload: payload["results"][0]["adGroupAd"]["ad"]["responsiveSearchAd"][
                "headlines"
            ][0].update(pinnedField=""),
        ),
        (
            "FROM campaign_criterion",
            lambda payload: payload["results"].append(copy.deepcopy(payload["results"][0])),
        ),
    )
    for query_marker, mutation in mutations:

        class DriftResponder(_Responder):
            def __call__(self, url, *, headers, json, timeout, allow_redirects):
                response = super().__call__(
                    url,
                    headers=headers,
                    json=json,
                    timeout=timeout,
                    allow_redirects=allow_redirects,
                )
                if query_marker in json.get("query", ""):
                    mutation(response._payload)
                return response

        with pytest.raises(GoogleAdsProviderError) as exc_info:
            _provider(DriftResponder()).find_by_contract_label(contract_label(CONTRACT))
        assert exc_info.value.code is ProviderErrorCode.READBACK_MISMATCH


def test_label_description_drift_fails_closed():
    class DriftResponder(_Responder):
        def __call__(self, url, *, headers, json, timeout, allow_redirects):
            response = super().__call__(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if "FROM campaign_label" in json.get("query", ""):
                response._payload["results"][0]["label"]["description"] = "drifted"
            return response

    with pytest.raises(GoogleAdsProviderError) as exc_info:
        _provider(DriftResponder()).find_by_contract_label(contract_label(CONTRACT))

    assert exc_info.value.code is ProviderErrorCode.READBACK_MISMATCH


def test_duplicate_label_or_paginated_readback_is_ambiguous_and_never_selects_a_resource():
    class AmbiguousResponder(_Responder):
        def __call__(self, url, *, headers, json, timeout, allow_redirects):
            response = super().__call__(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if "FROM campaign_label" in json.get("query", ""):
                response._payload["results"].append(copy.deepcopy(response._payload["results"][0]))
            return response

    with pytest.raises(GoogleAdsProviderError) as duplicate:
        _provider(AmbiguousResponder()).find_by_contract_label(contract_label(CONTRACT))
    assert duplicate.value.code is ProviderErrorCode.AMBIGUOUS_LABEL

    class PaginatedResponder(_Responder):
        def __call__(self, url, *, headers, json, timeout, allow_redirects):
            response = super().__call__(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if "FROM ad_group" in json.get("query", ""):
                response._payload["nextPageToken"] = "must-not-log"
            return response

    with pytest.raises(GoogleAdsProviderError) as paginated:
        _provider(PaginatedResponder()).find_by_contract_label(contract_label(CONTRACT))
    assert paginated.value.code is ProviderErrorCode.AMBIGUOUS_READBACK
    assert "must-not-log" not in str(paginated.value)


def test_adapter_rejects_any_graph_drift_or_activation_shape_before_transport():
    responder = _Responder()
    provider = _provider(responder)
    request = build_mutate_request(CONTRACT, CUSTOMER_ID, validate_only=False)
    request["mutateOperations"][2]["campaignOperation"]["create"]["status"] = "ENABLED"

    with pytest.raises(GoogleAdsProviderError) as exc_info:
        provider.create_paused(request)

    assert responder.calls == []
    assert exc_info.value.code is ProviderErrorCode.INVALID_GRAPH
    assert "ENABLED" not in str(exc_info.value)


def test_adapter_exposes_no_generic_mutation_or_activation_operation():
    public_methods = {
        name
        for name, member in inspect.getmembers(GoogleAdsV25PausedProvider, inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_methods == {"validate", "find_by_contract_label", "create_paused"}


def test_create_transport_failure_is_ambiguous_and_contains_only_enum_and_request_hash():
    calls = 0

    def fail(_url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _Response()
        raise RuntimeError(
            "customer=1234567890 token=do-not-leak request-id=raw-request-id-must-not-escape"
        )

    provider = _provider(fail)
    provider.validate(build_mutate_request(CONTRACT, CUSTOMER_ID, validate_only=True))
    request = build_mutate_request(CONTRACT, CUSTOMER_ID, validate_only=False)

    with pytest.raises(AmbiguousProviderTimeout) as exc_info:
        provider.create_paused(request)

    error = exc_info.value
    assert error.code is ProviderErrorCode.AMBIGUOUS_CREATE
    assert error.request_hash.startswith("sha256:")
    assert set(vars(error)) == {"code", "request_hash"}
    for forbidden in (CUSTOMER_ID, "do-not-leak", "raw-request-id"):
        assert forbidden not in str(error)


@pytest.mark.parametrize("customer_id", ["", "123", "123-45", "abcdefghij"])
def test_customer_identifiers_are_validated_before_credentials_or_transport(customer_id):
    calls = []
    with pytest.raises(ValueError, match="exactly 10 digits"):
        GoogleAdsV25PausedProvider(
            customer_id=customer_id,
            developer_token="fake",
            login_customer_id=None,
            contract=CONTRACT,
            credential_loader=lambda **_kwargs: calls.append("credentials"),
            requester=lambda *_args, **_kwargs: calls.append("transport"),
        )
    assert calls == []
