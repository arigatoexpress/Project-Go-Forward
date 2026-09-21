"""Execute the real deployment verifier with network and smoke commands mocked."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

EXPECTED_SHA = "a" * 40
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml"


def run_verifier(body, *, curl_exit=0, expected_sha=EXPECTED_SHA):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(
        step
        for step in workflow["jobs"]["build-and-deploy"]["steps"]
        if step.get("name") == "Verify deployment"
    )["run"]
    # GitHub normally expands these before bash executes the step.
    step = re.sub(r"\$\{\{[^}]+\}\}", "smoke", step)
    mocks = r"""
gcloud() { printf '%s\n' 'https://candidate.example.test'; }
python() { return 0; }
python3() { "$SMOKE_PYTHON" "$@"; }
curl() {
    case " $* " in
        *" -X POST "*) printf '%s\n' 'POSTED_COMMIT_STATUS'; return 0 ;;
    esac
    printf '%s' "$SMOKE_HEALTHZ_BODY"
    return "$SMOKE_CURL_EXIT"
}
"""
    return subprocess.run(
        ["bash", "-c", mocks + step],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "PATH": os.defpath,
            "SMOKE_PYTHON": sys.executable,
            "SMOKE_HEALTHZ_BODY": body,
            "SMOKE_CURL_EXIT": str(curl_exit),
            "GH_SHA": expected_sha,
            "GH_SERVER": "https://github.example.test",
            "GH_REPO": "example/project",
            "GH_RUN_ID": "1",
            "GITHUB_TOKEN": "smoke",
        },
    )


def test_exact_candidate_sha_allows_success_status():
    result = run_verifier(json.dumps({"version": EXPECTED_SHA}))

    assert result.returncode == 0, result.stderr
    assert "POSTED_COMMIT_STATUS" in result.stdout


@pytest.mark.parametrize(
    ("body", "curl_exit", "expected_sha"),
    [
        (json.dumps({"version": "b" * 40}), 0, EXPECTED_SHA),
        ("{}", 0, EXPECTED_SHA),
        ('{"version":""}', 0, EXPECTED_SHA),
        ("not json", 0, EXPECTED_SHA),
        (json.dumps({"version": EXPECTED_SHA}), 22, EXPECTED_SHA),
        ('{"version":""}', 0, ""),
    ],
    ids=["wrong-sha", "missing-sha", "empty-sha", "invalid-json", "http-error", "no-expected-sha"],
)
def test_unverified_candidate_fails_before_posting_success(body, curl_exit, expected_sha):
    result = run_verifier(body, curl_exit=curl_exit, expected_sha=expected_sha)

    assert result.returncode != 0
    assert "POSTED_COMMIT_STATUS" not in result.stdout
