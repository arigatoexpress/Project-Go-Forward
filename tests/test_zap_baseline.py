"""The public ZAP baseline reads one production origin and fails only on high alerts."""

import json
from pathlib import Path

import pytest
import yaml

from scripts.zap_baseline import (
    TargetRejected,
    classify_alerts,
    load_rules,
    main,
    normalize_origin,
    production_origin,
    summary_text,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/zap-baseline.yml"
RULES = REPO / ".zap/rules.tsv"
TARGET = "https://www.texashomeoutlet.com"


def _report(alerts, host="www.texashomeoutlet.com"):
    return {
        "site": [
            {
                "@host": host,
                "@name": f"https://{host}",
                "alerts": alerts,
            }
        ]
    }


def _alert(plugin, risk, name="Example", url=f"{TARGET}/inventory"):
    return {
        "pluginid": plugin,
        "name": name,
        "riskcode": str(risk),
        "riskdesc": {
            3: "High (Medium)",
            2: "Medium (High)",
            1: "Low (Medium)",
            0: "Informational (Medium)",
        }[risk],
        "url": url,
    }


def test_production_origin_is_the_deploy_verify_site():
    assert production_origin(REPO) == TARGET


def test_production_origin_rejects_disagreement(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github/workflows/deploy.yml").write_text(
        '--canonical-origin "https://www.texashomeoutlet.com"\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts/production_smoke.py").write_text(
        'DEFAULT_BASE_URL = "https://www.texashomeoutlet.com"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        'business:\n  website: "https://example.test"\n',
        encoding="utf-8",
    )
    with pytest.raises(TargetRejected, match="disagree"):
        production_origin(tmp_path)


def test_production_origin_rejects_a_candidate_host(tmp_path):
    candidate = "https://candidate---project-go-forward-example.a.run.app"
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github/workflows/deploy.yml").write_text(
        f'--canonical-origin "{candidate}"\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts/production_smoke.py").write_text(
        f'DEFAULT_BASE_URL = "{candidate}"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        f'business:\n  website: "{candidate}"\n',
        encoding="utf-8",
    )
    with pytest.raises(TargetRejected, match="Cloud Run"):
        production_origin(tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        "http://www.texashomeoutlet.com",
        "https://user:pass@www.texashomeoutlet.com",  # pragma: allowlist secret
        "https://www.texashomeoutlet.com/inventory",
        "https://www.texashomeoutlet.com/?q=1",
        "https://127.0.0.1",
        "https://www.texashomeoutlet.com:443",
        "https://www.texashomeoutlet.com/`id`",
    ],
)
def test_normalize_origin_rejects_anything_but_the_site_root(value):
    with pytest.raises(TargetRejected):
        normalize_origin(value)


def test_rules_file_ignores_only_commented_false_positives():
    rules = load_rules(RULES)
    assert rules == {
        "10015": "IGNORE",
        "10049": "IGNORE",
        "10096": "IGNORE",
        "10109": "IGNORE",
        "10202": "IGNORE",
    }
    previous = ""
    for line in RULES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            previous = line
            continue
        assert previous.startswith("#")
        assert "\t" in line
        previous = ""


def test_gate_fails_only_on_high_alerts_outside_the_rules_file():
    rules = load_rules(RULES)
    highs, counts = classify_alerts(
        _report(
            [
                _alert("90034", 3, "Cloud Metadata Potentially Exposed"),
                _alert("10038", 2, "Content Security Policy"),
                _alert("10021", 1, "X-Content-Type-Options Header Missing"),
                _alert("10109", 0, "Modern Web Application"),
                _alert("10202", 3, "Absence of Anti-CSRF Tokens"),
            ]
        ),
        target=TARGET,
        rules=rules,
    )
    assert [alert["pluginid"] for alert in highs] == ["90034"]
    assert counts == {"high": 1, "medium": 1, "low": 1, "informational": 0, "ignored": 2}


def test_gate_passes_when_only_medium_and_ignored_alerts_remain():
    highs, counts = classify_alerts(
        _report([_alert("10038", 2), _alert("10096", 1, "Timestamp Disclosure - Unix")]),
        target=TARGET,
        rules=load_rules(RULES),
    )
    assert highs == []
    assert counts["high"] == 0
    assert counts["medium"] == 1
    assert counts["ignored"] == 1


def test_gate_rejects_a_report_that_names_another_host():
    with pytest.raises(ValueError, match="limited to"):
        classify_alerts(
            _report([_alert("90034", 3)], host="candidate---app.a.run.app"),
            target=TARGET,
            rules={},
        )


def test_summary_keeps_query_strings_out_of_the_log():
    text = summary_text(
        TARGET,
        [_alert("90034", 3, url=f"{TARGET}/staff?token=secret-value")],
        {"high": 1, "medium": 0, "low": 0, "informational": 0, "ignored": 0},
    )
    assert "/staff" in text
    assert "secret-value" not in text


def test_gate_cli_exit_codes(tmp_path, monkeypatch):
    report = tmp_path / "report_json.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report.write_text(json.dumps(_report([_alert("10038", 2)])), encoding="utf-8")
    assert (
        main(
            [
                "gate",
                "--report",
                str(report),
                "--rules",
                str(RULES),
                "--target",
                TARGET,
            ]
        )
        == 0
    )
    assert "No high-severity alerts" in summary.read_text(encoding="utf-8")

    report.write_text(
        json.dumps(_report([_alert("90034", 3, "Cloud Metadata Potentially Exposed")])),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "gate",
                "--report",
                str(report),
                "--rules",
                str(RULES),
                "--target",
                TARGET,
            ]
        )
        == 1
    )


def test_workflow_is_weekly_manual_passive_and_not_a_pr_check():
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    triggers = workflow[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "pull_request" not in text
    assert "secrets." not in text
    assert "texashomeoutlet.com" not in text
    assert "run.app" not in text
    assert "zap-full-scan" not in text
    assert "action-full-scan" not in text
    assert "action-api-scan" not in text

    step = next(
        item
        for item in workflow["jobs"]["baseline"]["steps"]
        if item.get("name") == "Passive baseline scan"
    )
    assert step["uses"].startswith("zaproxy/action-baseline@")
    assert step["with"]["docker_name"] == "ghcr.io/zaproxy/zaproxy:stable"
    assert step["with"]["cmd_options"] == "-I -m 1 -T 20"
    assert step["with"]["fail_action"] is False
    assert step["with"]["allow_issue_writing"] is False
    assert step["with"]["rules_file_name"] == ".zap/rules.tsv"
    assert step["with"]["artifact_name"] == "zap-baseline-report"
    assert step["with"]["target"] == "${{ env.ZAP_TARGET }}"
    assert workflow["permissions"] == {"contents": "read"}
