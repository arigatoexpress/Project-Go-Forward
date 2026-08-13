#!/usr/bin/env python3
"""Sanitized, read-only Google Ads account-access and USD-currency probe.

The command is offline unless ``--live`` is supplied. A live probe uses scoped
Application Default Credentials (ADC) and a one-row read query. It never prints
customer IDs, access tokens, developer tokens, response bodies, or request IDs.
It does not create or mutate an Ads resource and can never enable spend.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
from typing import Any

import google.auth
import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
ADS_API_VERSION = "v25"
QUERY = "SELECT customer.id, customer.currency_code FROM customer LIMIT 1"


def normalize_customer_id(value: str) -> str:
    """Return a hyphen-free Google Ads customer ID without ever logging it."""
    normalized = value.strip().replace("-", "")
    if not re.fullmatch(r"\d{10}", normalized):
        raise ValueError("Google Ads customer IDs must contain exactly 10 digits")
    return normalized


def _result(
    *,
    status: int | None,
    failure: str | None,
    account_currency_usd: bool = False,
) -> dict[str, Any]:
    return {
        "account_access_validated": status == 200,
        "account_currency_usd": account_currency_usd,
        "failure": failure,
        "http_status": status,
        "live_probe_executed": True,
        "request_id_present": False,
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def probe_access(
    *,
    customer_id: str,
    developer_token: str,
    login_customer_id: str | None = None,
    credential_loader: Callable[..., tuple[Any, str | None]] = google.auth.default,
    auth_request_factory: Callable[[], Any] = GoogleAuthRequest,
    requester: Callable[..., Any] = requests.post,
) -> dict[str, Any]:
    """Run one read-only query and return a sanitized status object."""
    normalized_customer_id = normalize_customer_id(customer_id)
    login_customer = normalize_customer_id(login_customer_id) if login_customer_id else None
    if not developer_token.strip():
        raise ValueError("Google Ads developer token is required")

    try:
        credentials, _project = credential_loader(scopes=[ADS_SCOPE])
        credentials.refresh(auth_request_factory())
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
            "developer-token": developer_token,
        }
        if login_customer:
            headers["login-customer-id"] = login_customer
        response = requester(
            (
                f"https://googleads.googleapis.com/{ADS_API_VERSION}/"
                f"customers/{normalized_customer_id}/googleAds:search"
            ),
            headers=headers,
            json={"query": QUERY},
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - the sanitized CLI must not expose provider errors
        return _result(status=None, failure="credential_or_network_error")

    if response.status_code == 200:
        try:
            payload = response.json()
            results = payload.get("results") if isinstance(payload, dict) else None
            customer_payload = (
                results[0].get("customer")
                if isinstance(results, list) and len(results) == 1 and isinstance(results[0], dict)
                else None
            )
            currency_is_usd = (
                isinstance(customer_payload, dict)
                and customer_payload.get("id") == normalized_customer_id
                and customer_payload.get("currencyCode") == "USD"
            )
        except Exception:
            currency_is_usd = False
        result = _result(
            status=200,
            failure=None if currency_is_usd else "account_currency_not_usd_or_unverified",
            account_currency_usd=currency_is_usd,
        )
    elif response.status_code in {401, 403}:
        result = _result(
            status=response.status_code,
            failure="authentication_or_access_denied",
        )
    elif 400 <= response.status_code < 500:
        result = _result(status=response.status_code, failure="request_rejected")
    else:
        result = _result(status=response.status_code, failure="google_ads_unavailable")
    result["request_id_present"] = bool(response.headers.get("request-id"))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="perform the one-row, read-only Google Ads request",
    )
    args = parser.parse_args(argv)

    if not args.live:
        print(
            json.dumps(
                {
                    "account_access_validated": False,
                    "account_currency_usd": False,
                    "live_probe_executed": False,
                    "ready_to_spend": False,
                    "spend_enabled": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    try:
        result = probe_access(
            customer_id=os.environ.get("GOOGLE_ADS_CUSTOMER_ID", ""),
            developer_token=os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            login_customer_id=os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None,
        )
    except ValueError:
        result = {
            "account_access_validated": False,
            "account_currency_usd": False,
            "failure": "invalid_configuration",
            "live_probe_executed": False,
            "ready_to_spend": False,
            "spend_enabled": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0
        if result["account_access_validated"] and result.get("account_currency_usd") is True
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
