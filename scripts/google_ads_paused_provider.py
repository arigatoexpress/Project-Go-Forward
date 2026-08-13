#!/usr/bin/env python3
"""Official Google Ads v25 REST adapter for one immutable PAUSED graph.

The adapter has no activation operation and accepts only the exact request
graph derived from the checked-in contract. It performs a successful
``validateOnly`` mutation before the create mutation, ignores provider IDs in
the mutation response, and accepts creation/reconciliation only after a full
readback of the labeled campaign matches the reviewed graph.

Raw customer IDs, resource names, credentials, provider responses, request
IDs, and provider errors are process-local only. Every raised error is reduced
to an allowlisted enum and a deterministic request hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

import google.auth
import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

from scripts.google_ads_access_probe import ADS_SCOPE, normalize_customer_id
from scripts.google_ads_launch_draft import (
    GOOGLE_ADS_API_VERSION,
    build_mutate_request,
    canonical_contract_json,
    contract_sha256,
    validate_draft,
)
from scripts.google_ads_paused_worker import (
    AmbiguousProviderTimeout,
    ProviderPausedDeployment,
    contract_label,
)

ADS_API_VERSION = GOOGLE_ADS_API_VERSION
ADS_API_ROOT = f"https://googleads.googleapis.com/{ADS_API_VERSION}"
REQUEST_TIMEOUT_SECONDS = 30
SEARCH_PAGE_SIZE = 1000
_CONTRACT_LABEL_RE = re.compile(r"^tho-contract-[0-9a-f]{12}$")


class ProviderErrorCode(StrEnum):
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    INVALID_GRAPH = "INVALID_GRAPH"
    CREDENTIAL_ERROR = "CREDENTIAL_ERROR"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    AMBIGUOUS_CREATE = "AMBIGUOUS_CREATE"
    AMBIGUOUS_LABEL = "AMBIGUOUS_LABEL"
    AMBIGUOUS_READBACK = "AMBIGUOUS_READBACK"
    ACCOUNT_CURRENCY_UNVERIFIED = "ACCOUNT_CURRENCY_UNVERIFIED"
    READBACK_MISMATCH = "READBACK_MISMATCH"


@dataclass(frozen=True)
class ProviderFailure:
    code: ProviderErrorCode
    request_hash: str


class GoogleAdsProviderError(RuntimeError):
    """Sanitized provider failure containing no raw provider material."""

    def __init__(self, code: ProviderErrorCode, request_hash: str):
        self.code = code
        self.request_hash = request_hash
        super().__init__(f"{code.value}:{request_hash}")


class GoogleAdsAmbiguousCreate(AmbiguousProviderTimeout):
    """Create outcome is unknown; callers must reconcile and never retry."""

    def __init__(self, request_hash: str):
        self.code = ProviderErrorCode.AMBIGUOUS_CREATE
        self.request_hash = request_hash
        super().__init__(f"{self.code.value}:{request_hash}")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sorted(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        values, key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    )


def _required_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("mapping required")
    return value


def _required_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("list required")
    return value


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("string required")
    return value


def _required_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("boolean required")
    return value


def _protobuf_bool(mapping: dict[str, Any], key: str) -> bool:
    """Normalize proto3 JSON's omitted default scalar to ``False``."""
    if key not in mapping:
        return False
    return _required_bool(mapping[key])


def _required_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("integer required")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    raise ValueError("integer required")


def _resource(value: Any, customer_id: str, resource_type: str) -> str:
    candidate = _required_string(value)
    pattern = rf"^customers/{re.escape(customer_id)}/{resource_type}/\d+$"
    if not re.fullmatch(pattern, candidate):
        raise ValueError("resource identity invalid")
    return candidate


def _asset_texts(value: Any) -> list[dict[str, str]]:
    return sorted(
        (
            {
                "text": _required_string(_required_mapping(asset).get("text")),
                "pinnedField": (
                    _required_string(_required_mapping(asset)["pinnedField"])
                    if "pinnedField" in _required_mapping(asset)
                    else "UNSPECIFIED"
                ),
            }
            for asset in _required_list(value)
        ),
        key=lambda asset: (asset["text"], asset["pinnedField"]),
    )


def _expected_snapshot(contract: dict[str, Any]) -> dict[str, Any]:
    campaign = _required_mapping(contract.get("campaign"))
    budget = _required_mapping(campaign.get("budget"))
    bidding = _required_mapping(campaign.get("bidding"))
    geo = _required_mapping(campaign.get("geo"))
    center = _required_mapping(geo.get("center"))
    tracking = _required_mapping(campaign.get("tracking"))
    final_url_suffix = "&".join(
        f"{key}={tracking[key]}"
        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
    )
    campaign_criteria = [
        {
            "status": "PAUSED",
            "negative": False,
            "proximity": {
                "geoPoint": {
                    "latitudeInMicroDegrees": int(
                        Decimal(str(center["latitude"])) * Decimal(1_000_000)
                    ),
                    "longitudeInMicroDegrees": int(
                        Decimal(str(center["longitude"])) * Decimal(1_000_000)
                    ),
                },
                "radius": int(geo["radius_miles"]),
                "radiusUnits": "MILES",
            },
        }
    ]
    campaign_criteria.extend(
        {
            "status": "PAUSED",
            "negative": True,
            "keyword": {
                "text": keyword["text"],
                "matchType": keyword["match_type"],
            },
        }
        for keyword in campaign["negative_keywords"]
    )
    ad_groups = []
    for group in campaign["ad_groups"]:
        ad = group["responsive_search_ad"]
        ad_groups.append(
            {
                "name": group["name"],
                "status": "PAUSED",
                "type": "SEARCH_STANDARD",
                "keywords": _sorted(
                    [
                        {
                            "status": "PAUSED",
                            "type": "KEYWORD",
                            "negative": False,
                            "text": keyword["text"],
                            "matchType": keyword["match_type"],
                        }
                        for keyword in group["keywords"]
                    ]
                ),
                "ads": [
                    {
                        "status": "PAUSED",
                        "finalUrls": [ad["final_url"]],
                        "headlines": sorted(
                            (
                                {"text": text, "pinnedField": "UNSPECIFIED"}
                                for text in ad["headlines"]
                            ),
                            key=lambda asset: asset["text"],
                        ),
                        "descriptions": sorted(
                            (
                                {"text": text, "pinnedField": "UNSPECIFIED"}
                                for text in ad["descriptions"]
                            ),
                            key=lambda asset: asset["text"],
                        ),
                        "path1": ad["path1"],
                        "path2": ad["path2"],
                    }
                ],
            }
        )
    return {
        "campaign": {
            "name": campaign["name"],
            "status": "PAUSED",
            "advertisingChannelType": "SEARCH",
            "budget": {
                "name": f"{campaign['name']} | Budget",
                "amountMicros": int(Decimal(str(budget["average_daily_usd"])) * Decimal(1_000_000)),
                "deliveryMethod": "STANDARD",
                "explicitlyShared": False,
            },
            "targetSpend": {
                "cpcBidCeilingMicros": int(
                    Decimal(str(bidding["max_cpc_usd"])) * Decimal(1_000_000)
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
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            "finalUrlSuffix": final_url_suffix,
        },
        "campaignCriteria": _sorted(campaign_criteria),
        "adGroups": _sorted(ad_groups),
    }


class GoogleAdsV25PausedProvider:
    """REST adapter restricted to one reviewed contract and PAUSED graph."""

    def __init__(
        self,
        *,
        customer_id: str,
        developer_token: str,
        login_customer_id: str | None,
        contract: dict[str, Any],
        credential_loader: Callable[..., tuple[Any, str | None]] = google.auth.default,
        auth_request_factory: Callable[[], Any] = GoogleAuthRequest,
        requester: Callable[..., Any] = requests.post,
    ):
        self._customer_id = normalize_customer_id(customer_id)
        self._login_customer_id = (
            normalize_customer_id(login_customer_id) if login_customer_id else None
        )
        if not isinstance(developer_token, str) or not developer_token.strip():
            raise ValueError("developer token is required")
        if not isinstance(contract, dict) or validate_draft(contract):
            raise ValueError("contract is invalid")
        self._developer_token = developer_token
        self._contract = json.loads(canonical_contract_json(contract))
        self._contract_hash = f"sha256:{contract_sha256(self._contract)}"
        self._contract_label = contract_label(self._contract)
        self._expected_snapshot = _expected_snapshot(self._contract)
        self._expected_snapshot_hash = _canonical_hash(self._expected_snapshot)
        self._expected_validation_request = build_mutate_request(
            self._contract,
            self._customer_id,
            validate_only=True,
        )
        self._expected_create_request = build_mutate_request(
            self._contract,
            self._customer_id,
            validate_only=False,
        )
        self._credential_loader = credential_loader
        self._auth_request_factory = auth_request_factory
        self._requester = requester
        self._access_token: str | None = None
        self._account_currency_verified = False
        self._validated_graph_hash: str | None = None
        self._last_failure: ProviderFailure | None = None

    @property
    def last_failure(self) -> ProviderFailure | None:
        return self._last_failure

    def _raise(self, code: ProviderErrorCode, request_hash: str) -> None:
        self._last_failure = ProviderFailure(code=code, request_hash=request_hash)
        raise GoogleAdsProviderError(code, request_hash)

    def _raise_ambiguous_create(self, request_hash: str) -> None:
        self._last_failure = ProviderFailure(
            code=ProviderErrorCode.AMBIGUOUS_CREATE,
            request_hash=request_hash,
        )
        raise GoogleAdsAmbiguousCreate(request_hash)

    def _headers(self, request_hash: str) -> dict[str, str]:
        if self._access_token is None:
            try:
                credentials, _project = self._credential_loader(scopes=[ADS_SCOPE])
                credentials.refresh(self._auth_request_factory())
                token = credentials.token
                if not isinstance(token, str) or not token:
                    raise ValueError("missing access token")
                self._access_token = token
            except Exception:
                self._raise(ProviderErrorCode.CREDENTIAL_ERROR, request_hash)
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "developer-token": self._developer_token,
        }
        if self._login_customer_id:
            headers["login-customer-id"] = self._login_customer_id
        return headers

    def _post(
        self,
        *,
        url: str,
        body: dict[str, Any],
        purpose: str,
    ) -> tuple[Any, str]:
        request_hash = _canonical_hash({"body": body, "purpose": purpose, "url": url})
        headers = self._headers(request_hash)
        try:
            response = self._requester(
                url,
                headers=headers,
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            status = response.status_code
        except GoogleAdsProviderError:
            raise
        except Exception:
            if purpose == "create":
                self._raise_ambiguous_create(request_hash)
            self._raise(ProviderErrorCode.PROVIDER_UNAVAILABLE, request_hash)
        if isinstance(status, bool) or not isinstance(status, int):
            if purpose == "create":
                self._raise_ambiguous_create(request_hash)
            self._raise(ProviderErrorCode.MALFORMED_RESPONSE, request_hash)
        if not 200 <= status < 300:
            if purpose == "create" and status >= 500:
                self._raise_ambiguous_create(request_hash)
            if purpose == "validate":
                self._raise(ProviderErrorCode.VALIDATION_REJECTED, request_hash)
            if 400 <= status < 500:
                self._raise(ProviderErrorCode.REQUEST_REJECTED, request_hash)
            self._raise(ProviderErrorCode.PROVIDER_UNAVAILABLE, request_hash)
        return response, request_hash

    def _assert_exact_request(self, request: dict[str, Any], *, validate_only: bool) -> str:
        expected = (
            self._expected_validation_request if validate_only else self._expected_create_request
        )
        request_hash = _canonical_hash(request)
        if request != expected:
            self._raise(ProviderErrorCode.INVALID_GRAPH, request_hash)
        return request_hash

    def validate(self, request: dict[str, Any]) -> None:
        request_hash = self._assert_exact_request(request, validate_only=True)
        if not self._account_currency_verified:
            self._raise(ProviderErrorCode.ACCOUNT_CURRENCY_UNVERIFIED, request_hash)
        self._post(
            url=f"{ADS_API_ROOT}/customers/{self._customer_id}/googleAds:mutate",
            body=request,
            purpose="validate",
        )
        self._validated_graph_hash = _canonical_hash(request["mutateOperations"])

    def create_paused(self, request: dict[str, Any]) -> ProviderPausedDeployment:
        request_hash = self._assert_exact_request(request, validate_only=False)
        if not self._account_currency_verified:
            self._raise(ProviderErrorCode.ACCOUNT_CURRENCY_UNVERIFIED, request_hash)
        if self._validated_graph_hash != _canonical_hash(request["mutateOperations"]):
            self._raise(ProviderErrorCode.INVALID_GRAPH, request_hash)
        self._post(
            url=f"{ADS_API_ROOT}/customers/{self._customer_id}/googleAds:mutate",
            body=request,
            purpose="create",
        )
        deployment = self.find_by_contract_label(self._contract_label)
        if deployment is None:
            self._raise_ambiguous_create(request_hash)
        return deployment

    def _search(self, query: str) -> tuple[list[dict[str, Any]], str]:
        body = {"query": query, "pageSize": SEARCH_PAGE_SIZE}
        response, request_hash = self._post(
            url=f"{ADS_API_ROOT}/customers/{self._customer_id}/googleAds:search",
            body=body,
            purpose="readback",
        )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("response must be a mapping")
            if payload.get("nextPageToken"):
                self._raise(ProviderErrorCode.AMBIGUOUS_READBACK, request_hash)
            results = payload.get("results", [])
            if not isinstance(results, list) or not all(isinstance(row, dict) for row in results):
                raise ValueError("results must be mappings")
            return results, request_hash
        except GoogleAdsProviderError:
            raise
        except Exception:
            self._raise(ProviderErrorCode.MALFORMED_RESPONSE, request_hash)

    def verify_account_currency_usd(self) -> None:
        """Prove the current credential/account pair uses reviewed USD before mutate."""
        self._account_currency_verified = False
        rows, request_hash = self._search(
            "SELECT customer.id, customer.currency_code FROM customer LIMIT 1"
        )
        try:
            if len(rows) != 1:
                raise ValueError("customer cardinality mismatch")
            customer = _required_mapping(rows[0].get("customer"))
            if (
                self._contract["campaign"].get("currency_code") != "USD"
                or customer.get("id") != self._customer_id
                or customer.get("currencyCode") != "USD"
            ):
                raise ValueError("account currency mismatch")
        except (KeyError, TypeError, ValueError):
            self._raise(ProviderErrorCode.ACCOUNT_CURRENCY_UNVERIFIED, request_hash)
        self._account_currency_verified = True

    def _label_campaign(self, label: str) -> tuple[str | None, str]:
        if label != self._contract_label or not _CONTRACT_LABEL_RE.fullmatch(label):
            self._raise(ProviderErrorCode.INVALID_GRAPH, _canonical_hash({"label": label}))
        rows, request_hash = self._search(
            "SELECT campaign.resource_name, label.name, label.description "
            "FROM campaign_label "
            f"WHERE label.name = '{label}' AND campaign.status != 'REMOVED'"
        )
        if not rows:
            return None, request_hash
        if len(rows) != 1:
            self._raise(ProviderErrorCode.AMBIGUOUS_LABEL, request_hash)
        try:
            row = rows[0]
            label_payload = _required_mapping(row.get("label"))
            if (
                label_payload.get("name") != label
                or label_payload.get("description")
                != "Texas Home Outlet immutable campaign contract"
            ):
                self._raise(ProviderErrorCode.READBACK_MISMATCH, request_hash)
            campaign_resource = _resource(
                _required_mapping(row.get("campaign")).get("resourceName"),
                self._customer_id,
                "campaigns",
            )
            return campaign_resource, request_hash
        except (TypeError, ValueError):
            self._raise(ProviderErrorCode.MALFORMED_RESPONSE, request_hash)

    def _readback_snapshot(self, campaign_resource: str) -> tuple[dict[str, Any], str]:
        campaign_rows, request_hash = self._search(
            "SELECT campaign.resource_name, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, campaign.campaign_budget, "
            "campaign.target_spend.cpc_bid_ceiling_micros, "
            "campaign.network_settings.target_google_search, "
            "campaign.network_settings.target_search_network, "
            "campaign.network_settings.target_content_network, "
            "campaign.network_settings.target_partner_search_network, "
            "campaign.network_settings.target_youtube, "
            "campaign.network_settings.target_google_tv_network, "
            "campaign.geo_target_type_setting.positive_geo_target_type, "
            "campaign.geo_target_type_setting.negative_geo_target_type, "
            "campaign.contains_eu_political_advertising, campaign.final_url_suffix, "
            "campaign_budget.resource_name, campaign_budget.name, "
            "campaign_budget.amount_micros, campaign_budget.delivery_method, "
            "campaign_budget.explicitly_shared "
            f"FROM campaign WHERE campaign.resource_name = '{campaign_resource}'"
        )
        campaign_criterion_rows, _ = self._search(
            "SELECT campaign_criterion.campaign, campaign_criterion.status, "
            "campaign_criterion.negative, "
            "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type, "
            "campaign_criterion.proximity.geo_point.latitude_in_micro_degrees, "
            "campaign_criterion.proximity.geo_point.longitude_in_micro_degrees, "
            "campaign_criterion.proximity.radius, campaign_criterion.proximity.radius_units "
            "FROM campaign_criterion "
            f"WHERE campaign.resource_name = '{campaign_resource}' "
            "AND campaign_criterion.status != 'REMOVED'"
        )
        ad_group_rows, _ = self._search(
            "SELECT ad_group.resource_name, ad_group.campaign, ad_group.name, "
            "ad_group.status, ad_group.type FROM ad_group "
            f"WHERE campaign.resource_name = '{campaign_resource}' "
            "AND ad_group.status != 'REMOVED'"
        )
        criterion_rows, _ = self._search(
            "SELECT ad_group.resource_name, ad_group_criterion.ad_group, "
            "ad_group_criterion.status, ad_group_criterion.type, "
            "ad_group_criterion.negative, "
            "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type "
            "FROM ad_group_criterion "
            f"WHERE campaign.resource_name = '{campaign_resource}' "
            "AND ad_group_criterion.status != 'REMOVED'"
        )
        ad_rows, _ = self._search(
            "SELECT ad_group.resource_name, ad_group_ad.ad_group, ad_group_ad.status, "
            "ad_group_ad.ad.final_urls, "
            "ad_group_ad.ad.responsive_search_ad.headlines, "
            "ad_group_ad.ad.responsive_search_ad.descriptions, "
            "ad_group_ad.ad.responsive_search_ad.path1, "
            "ad_group_ad.ad.responsive_search_ad.path2 FROM ad_group_ad "
            f"WHERE campaign.resource_name = '{campaign_resource}' "
            "AND ad_group_ad.status != 'REMOVED'"
        )
        try:
            if len(campaign_rows) != 1:
                raise ValueError("campaign cardinality mismatch")
            campaign_row = campaign_rows[0]
            campaign = _required_mapping(campaign_row.get("campaign"))
            budget = _required_mapping(campaign_row.get("campaignBudget"))
            if _resource(
                campaign.get("resourceName"), self._customer_id, "campaigns"
            ) != campaign_resource or _resource(
                campaign.get("campaignBudget"), self._customer_id, "campaignBudgets"
            ) != _resource(budget.get("resourceName"), self._customer_id, "campaignBudgets"):
                raise ValueError("campaign identity mismatch")
            snapshot_campaign = {
                "name": _required_string(campaign.get("name")),
                "status": _required_string(campaign.get("status")),
                "advertisingChannelType": _required_string(campaign.get("advertisingChannelType")),
                "budget": {
                    "name": _required_string(budget.get("name")),
                    "amountMicros": _required_int(budget.get("amountMicros")),
                    "deliveryMethod": _required_string(budget.get("deliveryMethod")),
                    "explicitlyShared": _protobuf_bool(budget, "explicitlyShared"),
                },
                "targetSpend": {
                    "cpcBidCeilingMicros": _required_int(
                        _required_mapping(campaign.get("targetSpend")).get("cpcBidCeilingMicros")
                    )
                },
                "networkSettings": {
                    key: _protobuf_bool(_required_mapping(campaign.get("networkSettings")), key)
                    for key in (
                        "targetGoogleSearch",
                        "targetSearchNetwork",
                        "targetContentNetwork",
                        "targetPartnerSearchNetwork",
                        "targetYouTube",
                        "targetGoogleTvNetwork",
                    )
                },
                "geoTargetTypeSetting": {
                    key: _required_string(
                        _required_mapping(campaign.get("geoTargetTypeSetting")).get(key)
                    )
                    for key in ("positiveGeoTargetType", "negativeGeoTargetType")
                },
                "containsEuPoliticalAdvertising": _required_string(
                    campaign.get("containsEuPoliticalAdvertising")
                ),
                "finalUrlSuffix": _required_string(campaign.get("finalUrlSuffix")),
            }

            normalized_campaign_criteria = []
            for row in campaign_criterion_rows:
                criterion = _required_mapping(row.get("campaignCriterion"))
                if criterion.get("campaign") != campaign_resource:
                    raise ValueError("criterion campaign mismatch")
                status = _required_string(criterion.get("status"))
                negative = _protobuf_bool(criterion, "negative")
                if "keyword" in criterion:
                    keyword = _required_mapping(criterion.get("keyword"))
                    normalized_campaign_criteria.append(
                        {
                            "status": status,
                            "negative": negative,
                            "keyword": {
                                "text": _required_string(keyword.get("text")),
                                "matchType": _required_string(keyword.get("matchType")),
                            },
                        }
                    )
                elif "proximity" in criterion:
                    proximity = _required_mapping(criterion.get("proximity"))
                    point = _required_mapping(proximity.get("geoPoint"))
                    normalized_campaign_criteria.append(
                        {
                            "status": status,
                            "negative": negative,
                            "proximity": {
                                "geoPoint": {
                                    "latitudeInMicroDegrees": _required_int(
                                        point.get("latitudeInMicroDegrees")
                                    ),
                                    "longitudeInMicroDegrees": _required_int(
                                        point.get("longitudeInMicroDegrees")
                                    ),
                                },
                                "radius": _required_int(proximity.get("radius")),
                                "radiusUnits": _required_string(proximity.get("radiusUnits")),
                            },
                        }
                    )
                else:
                    raise ValueError("unexpected campaign criterion")

            groups: dict[str, dict[str, Any]] = {}
            for row in ad_group_rows:
                group = _required_mapping(row.get("adGroup"))
                resource_name = _resource(group.get("resourceName"), self._customer_id, "adGroups")
                if resource_name in groups or group.get("campaign") != campaign_resource:
                    raise ValueError("ad group identity mismatch")
                groups[resource_name] = {
                    "name": _required_string(group.get("name")),
                    "status": _required_string(group.get("status")),
                    "type": _required_string(group.get("type")),
                    "keywords": [],
                    "ads": [],
                }
            for row in criterion_rows:
                criterion = _required_mapping(row.get("adGroupCriterion"))
                group_resource = _resource(criterion.get("adGroup"), self._customer_id, "adGroups")
                if group_resource not in groups:
                    raise ValueError("criterion group mismatch")
                keyword = _required_mapping(criterion.get("keyword"))
                groups[group_resource]["keywords"].append(
                    {
                        "status": _required_string(criterion.get("status")),
                        "type": _required_string(criterion.get("type")),
                        "negative": _protobuf_bool(criterion, "negative"),
                        "text": _required_string(keyword.get("text")),
                        "matchType": _required_string(keyword.get("matchType")),
                    }
                )
            for row in ad_rows:
                ad_group_ad = _required_mapping(row.get("adGroupAd"))
                group_resource = _resource(
                    ad_group_ad.get("adGroup"), self._customer_id, "adGroups"
                )
                if group_resource not in groups:
                    raise ValueError("ad group mismatch")
                ad = _required_mapping(ad_group_ad.get("ad"))
                responsive = _required_mapping(ad.get("responsiveSearchAd"))
                groups[group_resource]["ads"].append(
                    {
                        "status": _required_string(ad_group_ad.get("status")),
                        "finalUrls": [
                            _required_string(value) for value in _required_list(ad.get("finalUrls"))
                        ],
                        "headlines": _asset_texts(responsive.get("headlines")),
                        "descriptions": _asset_texts(responsive.get("descriptions")),
                        "path1": _required_string(responsive.get("path1")),
                        "path2": _required_string(responsive.get("path2")),
                    }
                )
            normalized_groups = []
            for group in groups.values():
                group["keywords"] = _sorted(group["keywords"])
                group["ads"] = _sorted(group["ads"])
                normalized_groups.append(group)
            return (
                {
                    "campaign": snapshot_campaign,
                    "campaignCriteria": _sorted(normalized_campaign_criteria),
                    "adGroups": _sorted(normalized_groups),
                },
                request_hash,
            )
        except (KeyError, TypeError, ValueError):
            self._raise(ProviderErrorCode.READBACK_MISMATCH, request_hash)

    def find_by_contract_label(self, label: str) -> ProviderPausedDeployment | None:
        campaign_resource, request_hash = self._label_campaign(label)
        if campaign_resource is None:
            return None
        snapshot, _ = self._readback_snapshot(campaign_resource)
        if _canonical_hash(snapshot) != self._expected_snapshot_hash:
            self._raise(ProviderErrorCode.READBACK_MISMATCH, request_hash)
        return ProviderPausedDeployment(
            contract_hash=self._contract_hash,
            campaign_resource_name=campaign_resource,
            status="PAUSED",
        )
