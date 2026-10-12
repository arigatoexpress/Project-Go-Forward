"""Transactional-email health must be safe, explicit, and least-privilege aware."""

import io
import urllib.error

import pytest

import email_service


class _Response:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        email_service._RESEND_LIVENESS_URL,
        status,
        "provider error",
        {},
        io.BytesIO(body),
    )


@pytest.mark.parametrize(
    ("api_key", "verifier_state", "expected"),
    [
        ("", "ok", "not_configured"),
        ("re_test", "invalid_key", "invalid_key"),
        ("re_test", "unreachable", "unreachable"),
        ("re_test", "ok", "ok"),
    ],
)
def test_email_liveness_has_exactly_four_states(api_key, verifier_state, expected):
    result = email_service.check_email_liveness(api_key=api_key, verify=lambda _key: verifier_state)

    assert result == {"ok": expected == "ok", "state": expected}


def test_read_only_probe_accepts_full_access_key_without_sending():
    seen = {}

    def opener(request, timeout):
        seen.update(method=request.get_method(), url=request.full_url, timeout=timeout)
        return _Response()

    state = email_service._probe_resend_key("re_secret", timeout=1.25, opener=opener)

    assert state == "ok"
    assert seen == {
        "method": "GET",
        "url": "https://api.resend.com/domains",
        "timeout": 1.25,
    }


def test_read_only_probe_accepts_valid_send_only_key():
    def opener(_request, timeout):
        del timeout
        raise _http_error(401, b'{"name":"restricted_api_key"}')

    assert email_service._probe_resend_key("re_send_only", opener=opener) == "ok"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, b'{"name":"missing_api_key"}', "invalid_key"),
        (403, b'{"name":"restricted_api_key"}', "invalid_key"),
        (403, b'{"name":"suspended_api_key"}', "invalid_key"),
        (429, b'{"name":"rate_limit_exceeded"}', "unreachable"),
        (500, b'{"name":"application_error"}', "unreachable"),
        (503, b'{"name":"service_unavailable"}', "unreachable"),
    ],
)
def test_provider_failures_are_classified_without_leaking_details(status, body, expected):
    def opener(_request, timeout):
        del timeout
        raise _http_error(status, body)

    result = email_service.check_email_liveness(
        "not-a-real-credential",
        verify=lambda key: email_service._probe_resend_key(key, opener=opener),
    )

    assert result["state"] == expected
    assert "not-a-real-credential" not in repr(result)
    assert "request" not in repr(result).lower()


def test_transport_failure_is_unreachable():
    def opener(_request, timeout):
        del timeout
        raise urllib.error.URLError("offline")

    assert email_service._probe_resend_key("re_test", opener=opener) == "unreachable"
