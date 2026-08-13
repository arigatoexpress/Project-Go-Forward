import json

import scripts.google_ads_access_probe as access_probe


class _Credentials:
    token = "access-token-do-not-leak"

    def __init__(self):
        self.refreshed = False

    def refresh(self, _request):
        self.refreshed = True


class _Response:
    def __init__(self, status_code, *, payload=None, body="customer-data-do-not-leak"):
        self.status_code = status_code
        self.text = body
        self.headers = {"request-id": "request-id-do-not-leak"}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("raw-response-do-not-leak")
        return self._payload


def test_default_cli_is_offline_and_never_loads_credentials(monkeypatch, capsys):
    monkeypatch.setattr(
        access_probe,
        "probe_access",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("live probe called")),
    )

    assert access_probe.main([]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "account_access_validated": False,
        "account_currency_usd": False,
        "live_probe_executed": False,
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def test_live_probe_uses_scoped_adc_and_returns_presence_only_result():
    credentials = _Credentials()
    calls = []

    def load_credentials(*, scopes):
        assert scopes == [access_probe.ADS_SCOPE]
        return credentials, "project-id-do-not-leak"

    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        assert url.endswith("/v25/customers/1234567890/googleAds:search")
        assert headers["Authorization"] == "Bearer access-token-do-not-leak"
        assert headers["developer-token"] == "developer-token-do-not-leak"
        assert headers["login-customer-id"] == "9999999999"
        assert json == {"query": "SELECT customer.id, customer.currency_code FROM customer LIMIT 1"}
        return _Response(
            200,
            payload={"results": [{"customer": {"id": "1234567890", "currencyCode": "USD"}}]},
        )

    result = access_probe.probe_access(
        customer_id="123-456-7890",
        developer_token="developer-token-do-not-leak",
        login_customer_id="999-999-9999",
        credential_loader=load_credentials,
        auth_request_factory=object,
        requester=post,
    )

    assert credentials.refreshed is True
    assert len(calls) == 1
    assert result == {
        "account_access_validated": True,
        "account_currency_usd": True,
        "failure": None,
        "http_status": 200,
        "live_probe_executed": True,
        "request_id_present": True,
        "ready_to_spend": False,
        "spend_enabled": False,
    }
    serialized = json.dumps(result)
    for secret in (
        "1234567890",
        "9999999999",
        "developer-token-do-not-leak",
        "access-token-do-not-leak",
        "customer-data-do-not-leak",
        "request-id-do-not-leak",
        "project-id-do-not-leak",
    ):
        assert secret not in serialized


def test_non_usd_or_unknown_currency_is_sanitized_and_never_green():
    credentials = _Credentials()

    for payload in (
        {"results": [{"customer": {"id": "1234567890", "currencyCode": "EUR"}}]},
        {"results": [{"customer": {"id": "0000000000", "currencyCode": "USD"}}]},
        {"results": [{"customer": {"currencyCode": "USD"}}]},
        {"results": [{"customer": {"id": "1234567890"}}]},
        {"results": []},
        {"rawCurrency": "EUR-do-not-leak"},
    ):
        result = access_probe.probe_access(
            customer_id="1234567890",
            developer_token="developer-token-do-not-leak",
            credential_loader=lambda **_kwargs: (credentials, None),
            auth_request_factory=object,
            requester=lambda *_args, **_kwargs: _Response(200, payload=payload),
        )

        assert result == {
            "account_access_validated": True,
            "account_currency_usd": False,
            "failure": "account_currency_not_usd_or_unverified",
            "http_status": 200,
            "live_probe_executed": True,
            "request_id_present": True,
            "ready_to_spend": False,
            "spend_enabled": False,
        }
        assert "EUR" not in json.dumps(result)


def test_live_cli_fails_closed_when_account_currency_is_not_verified(monkeypatch, capsys):
    monkeypatch.setattr(
        access_probe,
        "probe_access",
        lambda **_kwargs: {
            "account_access_validated": True,
            "account_currency_usd": False,
            "failure": "account_currency_not_usd_or_unverified",
            "http_status": 200,
            "live_probe_executed": True,
            "request_id_present": True,
            "ready_to_spend": False,
            "spend_enabled": False,
        },
    )

    assert access_probe.main(["--live"]) == 1
    assert json.loads(capsys.readouterr().out)["account_currency_usd"] is False


def test_denied_probe_does_not_echo_google_error_body():
    credentials = _Credentials()

    result = access_probe.probe_access(
        customer_id="1234567890",
        developer_token="developer-token-do-not-leak",
        credential_loader=lambda **_kwargs: (credentials, None),
        auth_request_factory=object,
        requester=lambda *_args, **_kwargs: _Response(
            403, body="USER_PERMISSION_DENIED customer=1234567890"
        ),
    )

    assert result["account_access_validated"] is False
    assert result["account_currency_usd"] is False
    assert result["failure"] == "authentication_or_access_denied"
    assert result["http_status"] == 403
    assert "USER_PERMISSION_DENIED" not in json.dumps(result)
    assert "1234567890" not in json.dumps(result)


def test_customer_ids_must_be_ten_digits():
    for value in ("", "123", "1234-567-8901", "not-an-id", "123 456 7890"):
        try:
            access_probe.normalize_customer_id(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid customer ID: {value!r}")
