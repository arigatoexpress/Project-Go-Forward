from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_starlette_testclient_transport_is_declared() -> None:
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()

    assert "httpx>=0.28.0" in requirements
    assert "httpx2>=2.7.0" in requirements


def test_raw_text_sanitizer_request_uses_content() -> None:
    source = (ROOT / "tests" / "test_input_sanitizer.py").read_text(encoding="utf-8")

    assert 'content="plain text body"' in source
    assert 'data="plain text body"' not in source
