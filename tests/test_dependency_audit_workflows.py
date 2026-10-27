"""Dependency-audit workflows fail on new findings, with one scoped exception."""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEDULED_WORKFLOW = ROOT / ".github/workflows/dependency-audit.yml"
PR_WORKFLOW = ROOT / ".github/workflows/pr-hardening.yml"
ADK_CAPPED_ADVISORY = "GHSA-xcq7-p3pf-p88h"


def _job(workflow_path: Path, name: str) -> dict:
    return yaml.safe_load(workflow_path.read_text())["jobs"][name]


def _step(workflow_path: Path, job: str, name: str) -> dict:
    return next(step for step in _job(workflow_path, job)["steps"] if step.get("name") == name)


def _run_step(
    script: str,
    tmp_path: Path,
    *,
    mocks: str,
    status: int,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "MOCK_CALLS": str(tmp_path / "calls.txt"),
        "MOCK_AUDIT_STATUS": str(status),
    }
    return subprocess.run(
        ["bash", "-c", mocks + script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_audit_jobs_are_not_continue_on_error_or_masked():
    scheduled = _job(SCHEDULED_WORKFLOW, "audit")
    pull_request = _job(PR_WORKFLOW, "dependency-audit")

    assert not scheduled.get("continue-on-error", False)
    assert not pull_request.get("continue-on-error", False)
    for job in (scheduled, pull_request):
        for step in job["steps"]:
            script = step.get("run", "")
            assert "|| true" not in script
            assert "(informational)" not in script


def test_backend_audits_share_one_explicit_advisory_exception():
    scripts = (
        _step(
            SCHEDULED_WORKFLOW,
            "audit",
            "pip-audit (runtime and development requirements)",
        )["run"],
        _step(PR_WORKFLOW, "dependency-audit", "Audit backend dependencies")["run"],
    )

    for script in scripts:
        assert "-r requirements.txt -r requirements-dev.txt" in script
        assert script.count("--ignore-vuln") == 1
        assert f"--ignore-vuln {ADK_CAPPED_ADVISORY}" in script


def test_pr_backend_audit_propagates_failure_and_records_arguments(tmp_path):
    step = _step(PR_WORKFLOW, "dependency-audit", "Audit backend dependencies")
    mocks = """
python() {
    printf '%s\n' "$*" >> "$MOCK_CALLS"
    printf 'mock backend audit\n'
    return "$MOCK_AUDIT_STATUS"
}
"""

    result = _run_step(step["run"], tmp_path, mocks=mocks, status=7)

    assert result.returncode == 7
    assert "Audit exit status: 7" in result.stdout
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    assert len(calls) == 1
    assert calls[0].startswith("-m pip_audit -r requirements.txt")
    assert f"--ignore-vuln {ADK_CAPPED_ADVISORY}" in calls[0]


def test_pr_frontend_audit_propagates_failure(tmp_path):
    step = _step(
        PR_WORKFLOW,
        "dependency-audit",
        "Audit frontend dependencies (high and critical)",
    )
    mocks = """
npm() {
    printf '%s\n' "$*" >> "$MOCK_CALLS"
    printf 'mock frontend audit\n'
    return "$MOCK_AUDIT_STATUS"
}
"""

    result = _run_step(
        step["run"],
        tmp_path,
        mocks=mocks,
        status=9,
        cwd=ROOT / "frontend",
    )

    assert result.returncode == 9
    assert (tmp_path / "calls.txt").read_text().strip() == "audit --audit-level=high"


def test_scheduled_backend_audit_propagates_failure(tmp_path):
    step = _step(
        SCHEDULED_WORKFLOW,
        "audit",
        "pip-audit (runtime and development requirements)",
    )
    mocks = """
python() { return 0; }
pip-audit() {
    printf '%s\n' "$*" >> "$MOCK_CALLS"
    printf 'mock scheduled backend audit\n'
    return "$MOCK_AUDIT_STATUS"
}
"""

    result = _run_step(step["run"], tmp_path, mocks=mocks, status=6)

    assert result.returncode == 6
    assert "mock scheduled backend audit" in (tmp_path / "summary.md").read_text()
    calls = (tmp_path / "calls.txt").read_text()
    assert "-r requirements.txt -r requirements-dev.txt" in calls
    assert f"--ignore-vuln {ADK_CAPPED_ADVISORY}" in calls


def test_scheduled_npm_audit_checks_both_projects_and_propagates_failure(tmp_path):
    step = _step(SCHEDULED_WORKFLOW, "audit", "npm audit (frontend, e2e)")
    mocks = """
npm() {
    printf '%s:%s\n' "$PWD" "$*" >> "$MOCK_CALLS"
    case "$PWD" in
        */frontend) return "$MOCK_AUDIT_STATUS" ;;
        *) return 0 ;;
    esac
}
"""

    result = _run_step(step["run"], tmp_path, mocks=mocks, status=4)

    assert result.returncode == 4
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    assert len(calls) == 2
    assert "/frontend:audit --package-lock-only --audit-level=high" in calls[0]
    assert "/e2e:audit --package-lock-only --audit-level=high" in calls[1]
