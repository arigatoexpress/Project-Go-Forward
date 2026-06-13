"""Smoke test that the load test script is importable."""

import scripts.load_test


def test_load_test_is_importable() -> None:
    assert hasattr(scripts.load_test, "_run")
    assert hasattr(scripts.load_test, "_LoadTest")
