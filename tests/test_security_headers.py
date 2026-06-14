"""CSP must be byte-identical to the long-standing baseline when no analytics
ID is set, and widen ONLY (and exactly) for validly-configured vendor IDs.

This guards the "strict no-op when unconfigured" invariant on the LIVE security
header — the live site's CSP must not change until an operator sets a valid ID.
"""

from test_api_v1 import create_client

_BASELINE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' https://d132mt2yijm03y.cloudfront.net https: data:; "
    "frame-src https://my.matterport.com; "
    "connect-src 'self'; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "frame-ancestors 'none'"
)

_ANALYTICS_ENV = ("GA4_MEASUREMENT_ID", "GTM_CONTAINER_ID", "META_PIXEL_ID", "TIKTOK_PIXEL_ID")


def _csp(client):
    return client.get("/healthz/").headers["content-security-policy"]


def test_csp_byte_identical_when_analytics_unconfigured(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    assert _csp(client) == _BASELINE_CSP


def test_csp_unchanged_for_malformed_id(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "garbage!!!")  # fails the format regex
    assert _csp(client) == _BASELINE_CSP


def test_csp_widens_only_for_configured_ga4(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    csp = _csp(client)
    assert "https://www.googletagmanager.com" in csp
    assert "https://www.google-analytics.com" in csp
    # unconfigured vendors must NOT be added
    assert "facebook" not in csp
    assert "tiktok" not in csp


def test_csp_widens_for_meta_and_tiktok(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("META_PIXEL_ID", "1234567890123456")
    monkeypatch.setenv("TIKTOK_PIXEL_ID", "CABCDEF1234567890GHIJK")
    csp = _csp(client)
    assert "https://connect.facebook.net" in csp
    assert "https://analytics.tiktok.com" in csp
