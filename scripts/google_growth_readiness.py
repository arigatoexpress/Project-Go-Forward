#!/usr/bin/env python3
"""Read-only readiness audit for THO's native Google growth stack.

The command checks API enablement, credential *presence*, a dedicated workload
identity, and runtime tag configuration. It never reads a secret payload,
prints an identifier value, or mutates Google Cloud / Google Ads state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable

SERVICES = (
    "googleads.googleapis.com",
    "searchconsole.googleapis.com",
    "analyticsadmin.googleapis.com",
    "analyticsdata.googleapis.com",
    "mybusinessbusinessinformation.googleapis.com",
    "businessprofileperformance.googleapis.com",
)

SECRETS = (
    "google-ads-developer-token",
    "google-ads-customer-id",
    "google-ads-login-customer-id",
    "google-ads-client-id",
    "google-ads-client-secret",
    "google-ads-refresh-token",
)

LEGACY_OAUTH_SECRETS = (
    "google-ads-client-id",
    "google-ads-client-secret",
    "google-ads-refresh-token",
)

ADS_SERVICE_ACCOUNT_ID = "google-growth-control"
ADS_JOB_ID = "google-growth-control"
JOB_SECRET_BINDINGS = {
    "GOOGLE_ADS_DEVELOPER_TOKEN": "google-ads-developer-token",
    "GOOGLE_ADS_CUSTOMER_ID": "google-ads-customer-id",
}

RUNTIME_NAMES = (
    "GA4_MEASUREMENT_ID",
    "GTM_CONTAINER_ID",
)


def _call(command: list[str], runner: Callable) -> tuple[bool, str]:
    completed = runner(command, capture_output=True, text=True, check=False)
    return completed.returncode == 0, completed.stdout if completed.returncode == 0 else ""


def _runtime_env_names(payload) -> set[str]:
    """Accept both gcloud's wrapped JSON and older direct-list output."""
    if isinstance(payload, dict):
        payload = (
            payload.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
            .get("env", [])
        )
    if not isinstance(payload, list):
        raise ValueError("unexpected Cloud Run env shape")
    return {item.get("name") for item in payload if isinstance(item, dict) and item.get("name")}


def _nested_values(payload, key: str) -> list:
    values = []
    if isinstance(payload, dict):
        for name, value in payload.items():
            if name == key:
                values.append(value)
            values.extend(_nested_values(value, key))
    elif isinstance(payload, list):
        for value in payload:
            values.extend(_nested_values(value, key))
    return values


def _job_runtime(payload, expected_service_account: str) -> dict[str, bool]:
    """Return presence-only checks for v1 or v2 Cloud Run Job JSON."""
    if not isinstance(payload, dict):
        raise ValueError("unexpected Cloud Run Job shape")

    service_accounts = {
        value
        for key in ("serviceAccount", "serviceAccountName")
        for value in _nested_values(payload, key)
        if isinstance(value, str)
    }
    containers = [
        container
        for value in _nested_values(payload, "containers")
        if isinstance(value, list)
        for container in value
        if isinstance(container, dict)
    ]
    if not containers:
        raise ValueError("Cloud Run Job has no container")

    args = [
        str(value)
        for container in containers
        for field in ("command", "args")
        for value in container.get(field, [])
        if isinstance(container.get(field), list)
    ]
    env_items = [
        item
        for container in containers
        for item in container.get("env", [])
        if isinstance(container.get("env"), list) and isinstance(item, dict)
    ]
    env_names = {
        item.get("name") for item in env_items if isinstance(item.get("name"), str)
    }
    secret_bindings = {}
    for item in env_items:
        value_source = item.get("valueSource") or item.get("valueFrom") or {}
        secret_ref = value_source.get("secretKeyRef", {}) if isinstance(value_source, dict) else {}
        if not isinstance(secret_ref, dict):
            continue
        secret_name = secret_ref.get("secret") or secret_ref.get("name")
        if isinstance(item.get("name"), str) and isinstance(secret_name, str):
            secret_bindings[item["name"]] = secret_name

    managed_secret_bindings = all(
        secret_bindings.get(env_name) == secret_name
        for env_name, secret_name in JOB_SECRET_BINDINGS.items()
    )
    identity_attached = expected_service_account in service_accounts
    live_probe_configured = "--live" in args
    persistent_key_path_absent = "GOOGLE_APPLICATION_CREDENTIALS" not in env_names
    return {
        "dedicated_identity_attached": identity_attached,
        "managed_secret_bindings": managed_secret_bindings,
        "live_probe_configured": live_probe_configured,
        "persistent_key_path_absent": persistent_key_path_absent,
        "runtime_ready": (
            identity_attached
            and managed_secret_bindings
            and live_probe_configured
            and persistent_key_path_absent
        ),
    }


def audit(project: str, service: str, region: str, *, runner=subprocess.run) -> dict:
    """Return presence-only readiness data. Command stderr is never retained."""
    project_flag = f"--project={project}"
    errors = []

    ok, output = _call(
        ["gcloud", "services", "list", "--enabled", project_flag, "--format=value(config.name)"],
        runner,
    )
    enabled = set(output.splitlines()) if ok else set()
    if not ok:
        errors.append("services")

    ok, output = _call(
        ["gcloud", "secrets", "list", project_flag, "--format=value(name)"],
        runner,
    )
    secret_names = set(output.splitlines()) if ok else set()
    if not ok:
        errors.append("secrets")

    ok, output = _call(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "list",
            project_flag,
            "--format=value(email)",
        ],
        runner,
    )
    service_account_emails = set(output.splitlines()) if ok else set()
    if not ok:
        errors.append("service_accounts")

    expected_service_account = (
        f"{ADS_SERVICE_ACCOUNT_ID}@{project}.iam.gserviceaccount.com"
    )
    dedicated_service_account = expected_service_account in service_account_emails
    persistent_user_key_absent = False
    if dedicated_service_account:
        ok, output = _call(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "list",
                f"--iam-account={expected_service_account}",
                "--managed-by=user",
                project_flag,
                "--format=value(name)",
            ],
            runner,
        )
        persistent_user_key_absent = ok and not output.strip()
        if not ok:
            errors.append("service_account_keys")

    ok, output = _call(
        [
            "gcloud",
            "run",
            "jobs",
            "list",
            f"--region={region}",
            project_flag,
            "--format=value(metadata.name)",
        ],
        runner,
    )
    job_names = {name.rsplit("/", 1)[-1] for name in output.splitlines()} if ok else set()
    if not ok:
        errors.append("jobs")
    dedicated_job_present = ADS_JOB_ID in job_names
    job_runtime = {
        "dedicated_identity_attached": False,
        "managed_secret_bindings": False,
        "live_probe_configured": False,
        "persistent_key_path_absent": True,
        "runtime_ready": False,
    }
    if dedicated_job_present:
        ok, output = _call(
            [
                "gcloud",
                "run",
                "jobs",
                "describe",
                ADS_JOB_ID,
                f"--region={region}",
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                job_runtime = _job_runtime(json.loads(output), expected_service_account)
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("job_runtime")
        else:
            errors.append("job_runtime")

    ok, output = _call(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            service,
            f"--region={region}",
            project_flag,
            "--format=json(spec.template.spec.containers[0].env)",
        ],
        runner,
    )
    runtime_names = set()
    if ok:
        try:
            runtime_names = _runtime_env_names(json.loads(output or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError, IndexError):
            errors.append("runtime")
    else:
        errors.append("runtime")

    secret_presence = {name: name in secret_names for name in SECRETS}
    legacy_user_oauth = all(secret_presence[name] for name in LEGACY_OAUTH_SECRETS)
    ads_account_config = (
        secret_presence["google-ads-developer-token"]
        and secret_presence["google-ads-customer-id"]
    )
    measurement = any(name in runtime_names for name in RUNTIME_NAMES)
    ads_api = "googleads.googleapis.com" in enabled
    seo_api = "searchconsole.googleapis.com" in enabled
    measurement_apis = all(
        name in enabled
        for name in ("analyticsadmin.googleapis.com", "analyticsdata.googleapis.com")
    )
    business_profile_apis = all(
        name in enabled
        for name in (
            "mybusinessbusinessinformation.googleapis.com",
            "businessprofileperformance.googleapis.com",
        )
    )
    google_ecosystem_apis = all(name in enabled for name in SERVICES)
    service_account_adc = (
        dedicated_service_account
        and persistent_user_key_absent
        and job_runtime["runtime_ready"]
    )
    ads_auth_path = service_account_adc
    presence_ready = (
        not errors
        and google_ecosystem_apis
        and ads_account_config
        and ads_auth_path
        and measurement
    )

    return {
        "project": project,
        "service": service,
        "region": region,
        "services": {name: name in enabled for name in SERVICES},
        "secrets": secret_presence,
        "service_account": {
            "dedicated_identity_present": dedicated_service_account,
            "persistent_user_key_absent": persistent_user_key_absent,
        },
        "job": {
            "dedicated_job_present": dedicated_job_present,
            **job_runtime,
        },
        "auth_paths": {
            "service_account_adc": service_account_adc,
            "legacy_user_oauth": legacy_user_oauth,
        },
        "runtime": {name: name in runtime_names for name in RUNTIME_NAMES},
        "readiness": {
            "ads_api": ads_api,
            "seo_api": seo_api,
            "measurement_apis": measurement_apis,
            "business_profile_apis": business_profile_apis,
            "google_ecosystem_apis": google_ecosystem_apis,
            "ads_account_config": ads_account_config,
            "ads_auth_path": ads_auth_path,
            "measurement": measurement,
            "presence_ready": presence_ready,
            # Presence cannot prove the identity was invited into Google Ads.
            "account_access_validated": False,
            "ready_to_spend": False,
        },
        "spend_enabled": False,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="tho-ai-agent")
    parser.add_argument("--service", default="project-go-forward")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 unless all prerequisites are present"
    )
    args = parser.parse_args()

    result = audit(args.project, args.service, args.region)
    print(json.dumps(result, indent=2, sort_keys=True))
    ready = result["readiness"]["presence_ready"]
    return 1 if args.strict and not ready else 0


if __name__ == "__main__":
    raise SystemExit(main())
