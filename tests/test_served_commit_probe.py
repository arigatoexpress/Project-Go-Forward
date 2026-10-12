"""Production smoke must compare the served and expected commits exactly."""

from scripts import production_smoke

EXPECTED = "a" * 40


def test_exact_served_commit_passes():
    probe = production_smoke.evaluate_served_commit(
        served=EXPECTED, expected=EXPECTED, status=200, elapsed_ms=3
    )

    assert probe.ok
    assert "match" in probe.evidence


def test_different_served_commit_fails():
    probe = production_smoke.evaluate_served_commit(
        served="b" * 40, expected=EXPECTED, status=200, elapsed_ms=3
    )

    assert not probe.ok
    assert "DRIFT" in probe.evidence


def test_prefix_is_not_an_exact_match():
    probe = production_smoke.evaluate_served_commit(
        served=EXPECTED, expected=EXPECTED[:7], status=200, elapsed_ms=3
    )

    assert not probe.ok


def test_unknown_served_commit_fails():
    probe = production_smoke.evaluate_served_commit(
        served=None, expected=EXPECTED, status=200, elapsed_ms=3
    )

    assert not probe.ok
    assert "serving=unknown" in probe.evidence


def test_cli_forwards_expected_commit(monkeypatch, capsys):
    seen = {}

    def fake_run_smoke(base_url, **kwargs):
        seen.update(base_url=base_url, **kwargs)
        return {"ok": True, "probes": []}

    monkeypatch.setattr(production_smoke, "run_smoke", fake_run_smoke)

    exit_code = production_smoke.main(
        ["--base-url", "https://example.test", "--expect-commit", EXPECTED]
    )

    assert exit_code == 0
    assert seen["expect_commit"] == EXPECTED
    assert '"ok": true' in capsys.readouterr().out


def test_check_served_commit_reads_public_health_version(monkeypatch):
    monkeypatch.setattr(
        production_smoke,
        "_json_probe",
        lambda *_args, **_kwargs: (200, {"version": EXPECTED}, 4),
    )

    probe = production_smoke.check_served_commit(
        "https://example.test", timeout=1.0, expected=EXPECTED
    )

    assert probe.ok
