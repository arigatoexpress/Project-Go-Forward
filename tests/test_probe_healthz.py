import http.client
import io
from urllib.error import HTTPError, URLError

import pytest

from scripts import probe_healthz

_GFE_502 = (
    b"\n<html><head><title>502 Server Error</title></head>"
    b"<body>Please try again in 30 seconds.</body></html>\n"
)
_OK_BODY = b'{"status":"ok","version":"abc"}'


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]):
        self.status = status
        self.headers = headers
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self._body[:limit]


def _http_error(code: int, body: bytes, content_type: str) -> HTTPError:
    return HTTPError(
        "https://example.test/healthz/",
        code,
        "error",
        {"content-type": content_type},
        io.BytesIO(body),
    )


@pytest.fixture
def probe_url(monkeypatch):
    monkeypatch.setenv("PROBE_URL", "https://example.test")
    monkeypatch.setattr(probe_healthz, "_pause", lambda _seconds: None)


def test_probe_retries_gateway_html_then_accepts_healthz(monkeypatch, probe_url, capsys):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(502, _GFE_502, "text/html; charset=UTF-8")
        return _Response(
            200,
            _OK_BODY,
            {"Cache-Control": "no-store", "content-type": "application/json"},
        )

    monkeypatch.setattr(probe_healthz.urllib.request, "urlopen", fake_urlopen)

    assert probe_healthz.probe(1) is None
    assert calls["n"] == 2
    logged = capsys.readouterr().err
    assert "status=502" in logged or "HTTP 502" in logged
    assert "text/html" in logged
    assert "502 Server Error" in logged
    assert "recovered" in logged


def test_probe_persistent_gateway_html_reports_body(monkeypatch, probe_url, capsys):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        raise _http_error(502, _GFE_502, "text/html; charset=UTF-8")

    monkeypatch.setattr(probe_healthz.urllib.request, "urlopen", fake_urlopen)

    message = probe_healthz.probe(4)

    assert calls["n"] == probe_healthz.TRANSIENT_ATTEMPTS
    assert message is not None
    assert "HTTP 502" in message
    assert "text/html; charset=UTF-8" in message
    assert "502 Server Error" in message
    assert "persistent failure" in capsys.readouterr().err


def test_probe_does_not_retry_missing_cache_control(monkeypatch, probe_url):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        return _Response(
            200, _OK_BODY, {"Cache-Control": "max-age=30", "content-type": "application/json"}
        )

    monkeypatch.setattr(probe_healthz.urllib.request, "urlopen", fake_urlopen)

    message = probe_healthz.probe(2)

    assert calls["n"] == 1
    assert message is not None
    assert "missing no-store" in message


def test_probe_retries_transport_error_then_fails(monkeypatch, probe_url):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        raise URLError("connection reset")

    monkeypatch.setattr(probe_healthz.urllib.request, "urlopen", fake_urlopen)

    message = probe_healthz.probe(3)

    assert calls["n"] == probe_healthz.TRANSIENT_ATTEMPTS
    assert message is not None
    assert "connection reset" in message


class _ReadFailure:
    """urlopen succeeded; the body read then failed."""

    def __init__(self, error: BaseException):
        self.status = 200
        self.headers = {"Cache-Control": "no-store", "content-type": "application/json"}
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("Connection reset by peer"),
        http.client.IncompleteRead(b'{"sta', 40),
    ],
)
def test_probe_retries_read_side_transport_error(monkeypatch, probe_url, error):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ReadFailure(error)
        return _Response(
            200,
            _OK_BODY,
            {"Cache-Control": "no-store", "content-type": "application/json"},
        )

    monkeypatch.setattr(probe_healthz.urllib.request, "urlopen", fake_urlopen)

    assert probe_healthz.probe(7) is None
    assert calls["n"] == 2


def test_parallel_probes_collect_read_resets_instead_of_aborting(monkeypatch, probe_url):
    monkeypatch.setattr(probe_healthz, "PROBE_COUNT", 4)

    def fake_urlopen(request, timeout):
        return _ReadFailure(ConnectionResetError("Connection reset by peer"))

    monkeypatch.setattr(probe_healthz.urllib.request, "urlopen", fake_urlopen)

    assert probe_healthz.main() == 1
