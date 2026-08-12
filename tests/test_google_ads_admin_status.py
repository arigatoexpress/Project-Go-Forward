import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from google_ads_admin.status import (
    CONTRACT_PATH,
    build_deployment_readiness,
)
from scripts.google_ads_launch_draft import contract_sha256

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "/api/admin/google-ads/deployment-readiness"
EXPECTED_STATES = [
    {"state": "INTERNAL_DRAFT", "status": "current"},
    {"state": "SERVER_VALIDATED", "status": "not_started"},
    {"state": "PAUSED_CREATE_APPROVED", "status": "locked"},
    {"state": "PAUSED_CREATED", "status": "locked"},
]
EXPECTED_BUDGET = {
    "average_daily_usd": 20,
    "max_single_day_charge_usd": 40,
    "monthly_charge_limit_usd": 608,
    "max_cpc_usd": 5,
}
EXPECTED_ACTIONS = {
    "review": False,
    "approve_paused_create": False,
    "create_paused": False,
    "activate": False,
}


def test_checked_in_contract_builds_exact_fail_closed_status():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    digest = contract_sha256(contract)

    status = build_deployment_readiness()

    assert status == {
        "schema_version": 1,
        "deployment_id": f"tho-search-high-intent-huffman-v1--{digest}",
        "deployment_key": "tho-search-high-intent-huffman-v1",
        "contract_hash": f"sha256:{digest}",
        "state": "INTERNAL_DRAFT",
        "state_source": "CHECKED_IN_CONTRACT",
        "connection": {"state": "NO_EVIDENCE", "verified_at": None},
        "feature_enabled": False,
        "ready": False,
        "spend_enabled": False,
        "budget": EXPECTED_BUDGET,
        "workflow": EXPECTED_STATES,
        "actions": EXPECTED_ACTIONS,
    }
    assert "ENABLED" not in json.dumps(status)


def test_status_is_deterministic_and_does_not_mutate_the_contract():
    before = CONTRACT_PATH.read_bytes()

    assert build_deployment_readiness() == build_deployment_readiness()
    assert CONTRACT_PATH.read_bytes() == before


def test_invalid_contract_fails_closed(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 3}', encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed Google Ads contract is invalid"):
        build_deployment_readiness(invalid)


def test_status_module_has_no_control_plane_or_network_imports():
    package = ROOT / "google_ads_admin"
    forbidden_imports = (
        "database",
        "google.ads",
        "google.auth",
        "google.cloud",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    )

    for source_path in package.glob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "google_ads_paused_worker" not in source
        assert "ledger" not in source.lower()
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(
            name == blocked or name.startswith(f"{blocked}.")
            for name in imported
            for blocked in forbidden_imports
        )


@pytest.fixture
def admin_client(monkeypatch):
    import main

    monkeypatch.setattr(main, "_verify_admin_token", lambda _token: True)
    return TestClient(main.app, raise_server_exceptions=False)


def test_status_endpoint_requires_admin_authentication():
    import main

    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.get(ENDPOINT)

    assert response.status_code == 401


def test_status_endpoint_is_get_only_and_returns_the_safe_contract_view(admin_client):
    response = admin_client.get(ENDPOINT, headers={"X-Admin-Token": "test-token"})

    assert response.status_code == 200
    assert response.json() == build_deployment_readiness()

    post_response = admin_client.post(
        ENDPOINT,
        headers={"X-Admin-Token": "test-token"},
        json={},
    )
    assert post_response.status_code == 405


def test_status_endpoint_sanitizes_contract_failures(admin_client, monkeypatch):
    import google_ads_admin.routes as routes

    monkeypatch.setattr(
        routes,
        "build_deployment_readiness",
        lambda: (_ for _ in ()).throw(RuntimeError("customer/123 raw-provider-secret")),
    )

    response = admin_client.get(ENDPOINT, headers={"X-Admin-Token": "test-token"})

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "message": "Paid Search deployment readiness is unavailable.",
        "status_code": 503,
    }
    assert "customer/123" not in response.text
    assert "raw-provider-secret" not in response.text
