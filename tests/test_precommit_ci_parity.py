"""PR CI runs the full pre-commit hook set over all tracked files (parity gate).

The local pre-commit hook only checks changed files, and repo-wide ruff
directory scans silently skip gitignored-but-tracked paths (scripts/ — 27
tracked files). This job closes both holes: hook-less commits cannot bypass
detect-secrets, and scripts/ can no longer drift out of format unseen.
"""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PR_WORKFLOW = ROOT / ".github/workflows/pr-hardening.yml"
HOOK_CONFIG = ROOT / ".pre-commit-config.yaml"
REQUIREMENTS_DEV = ROOT / "requirements-dev.txt"


def _job(workflow_path: Path, name: str) -> dict:
    return yaml.safe_load(workflow_path.read_text())["jobs"][name]


def _step(workflow_path: Path, job: str, name: str) -> dict:
    return next(step for step in _job(workflow_path, job)["steps"] if step.get("name") == name)


def _requirements_dev_pin(package: str) -> str:
    prefix = f"{package}=="
    pins = [
        line.strip()
        for line in REQUIREMENTS_DEV.read_text().splitlines()
        if line.strip().startswith(prefix)
    ]
    assert len(pins) == 1, f"expected exactly one {package} pin in requirements-dev.txt"
    return pins[0]


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


def test_precommit_job_is_not_continue_on_error_or_masked():
    job = _job(PR_WORKFLOW, "pre-commit")

    assert not job.get("continue-on-error", False)
    for step in job["steps"]:
        script = step.get("run", "")
        assert "|| true" not in script
        assert "(informational)" not in script


def test_precommit_install_pin_matches_requirements_dev():
    install = _step(PR_WORKFLOW, "pre-commit", "Install pre-commit")["run"]
    pin = _requirements_dev_pin("pre-commit")

    assert pin in install
    assert "pip install" in install


def test_precommit_run_step_covers_all_tracked_files():
    run = _step(PR_WORKFLOW, "pre-commit", "Run all hooks on all tracked files")["run"]

    assert "pre_commit run --all-files" in run or "pre-commit run --all-files" in run


def test_hook_config_covers_format_lint_and_secrets():
    config = HOOK_CONFIG.read_text()

    assert "id: ruff" in config
    assert "id: ruff-format" in config
    assert "id: detect-secrets" in config
    assert ".secrets.baseline" in config


def test_precommit_run_step_propagates_failure(tmp_path):
    step = _step(PR_WORKFLOW, "pre-commit", "Run all hooks on all tracked files")
    mocks = """
python() {
    printf '%s\n' "$*" >> "$MOCK_CALLS"
    printf 'mock pre-commit run\n'
    return "$MOCK_AUDIT_STATUS"
}
"""

    result = _run_step(step["run"], tmp_path, mocks=mocks, status=5)

    assert result.returncode == 5
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    assert len(calls) == 1
    assert calls[0] == "-m pre_commit run --all-files"
