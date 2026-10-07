"""Deployment identity: the Cloud Run service and GCP project every tool targets.

Production is Cloud Run service ``project-go-forward`` in ``tho-ai-agent``
(us-central1). ``tho-agent`` and ``sapphire-479610`` are retired targets.
"""

import json
import subprocess
from pathlib import Path

import pytest

import config_loader
from tools import health_check

REPO = Path(__file__).resolve().parent.parent
LIVE_SERVICE = "project-go-forward"
LIVE_PROJECT = "tho-ai-agent"
LIVE_REGION = "us-central1"


def test_firebase_hosting_rewrites_to_live_cloud_run_service():
    hosting = json.loads((REPO / "firebase.json").read_text())["hosting"]
    runs = [r["run"] for r in hosting["rewrites"] if "run" in r]
    assert runs, "firebase.json must rewrite to Cloud Run"
    for run in runs:
        assert run["serviceId"] == LIVE_SERVICE
        assert run["region"] == LIVE_REGION


def test_config_deployment_names_live_service_and_project():
    deployment = config_loader.get_deployment_config()
    assert deployment["service_name"] == LIVE_SERVICE
    assert deployment["project_id"] == LIVE_PROJECT
    assert deployment["region"] == LIVE_REGION


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    ).stdout.decode()
    return [REPO / p for p in out.split("\0") if p]


def test_no_tracked_config_or_code_targets_retired_service():
    try:
        files = _tracked_files()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git checkout not available")
    retired = (
        '"serviceId": "tho-agent"',
        'service_name: "tho-agent"',
        "gcloud run deploy tho-agent",
        "service_name=tho-agent",
        "tho-agent-s77j6bxyra",
        "tho-agent-691674245427",
        "tho-agent-trgi34bxuq",
    )
    offenders = []
    for path in files:
        if path.suffix not in {".py", ".json", ".yaml", ".yml", ".toml", ".js", ".jsx", ".md"}:
            continue
        if path.name == Path(__file__).name or "node_modules" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        offenders += [f"{path.relative_to(REPO)}: {s}" for s in retired if s in text]
    assert offenders == []


@pytest.fixture
def clean_project_env(monkeypatch):
    for var in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)


def test_health_check_project_defaults_to_config(clean_project_env):
    assert health_check.firestore_project_id() == LIVE_PROJECT


def test_health_check_project_falls_back_to_tho_ai_agent(clean_project_env, monkeypatch):
    monkeypatch.setattr(health_check, "get_deployment_config", lambda: {})
    assert health_check.firestore_project_id() == "tho-ai-agent"


def test_health_check_project_falls_back_when_config_unreadable(clean_project_env, monkeypatch):
    def boom():
        raise FileNotFoundError("config.yaml")

    monkeypatch.setattr(health_check, "get_deployment_config", boom)
    assert health_check.firestore_project_id() == "tho-ai-agent"


def test_health_check_project_env_overrides_config(clean_project_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-google-cloud-project")
    assert health_check.firestore_project_id() == "from-google-cloud-project"
    monkeypatch.setenv("GCP_PROJECT_ID", "from-gcp-project-id")
    assert health_check.firestore_project_id() == "from-gcp-project-id"


def test_health_check_firestore_client_uses_resolved_project(clean_project_env, monkeypatch):
    import google.cloud.firestore as firestore_module

    seen = {}

    class FakeClient:
        def __init__(self, project=None):
            seen["project"] = project

        def collection(self, _name):
            raise RuntimeError("stop after client construction")

    monkeypatch.setattr(firestore_module, "Client", FakeClient)
    monkeypatch.setattr(health_check, "check_endpoint", lambda *a, **k: {"ok": True})
    health_check.run_health_check()
    assert seen["project"] == LIVE_PROJECT


def test_health_check_source_has_no_hardcoded_project():
    source = (REPO / "tools" / "health_check.py").read_text()
    assert "sapphire-479610" not in source
