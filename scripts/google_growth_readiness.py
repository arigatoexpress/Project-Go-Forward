#!/usr/bin/env python3
"""Read-only readiness audit for THO's native Google growth stack.

The command checks API enablement, credential *presence*, a dedicated workload
identity, and runtime tag configuration. It never reads a secret payload,
prints an identifier value, or mutates Google Cloud / Google Ads state.
"""

from __future__ import annotations

import argparse
import json
import re
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
OPTIONAL_JOB_SECRET_BINDINGS = {
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "google-ads-login-customer-id",
}
EXPECTED_JOB_COMMAND = ("python",)
EXPECTED_JOB_ARGS = ("scripts/google_ads_access_evidence_job.py",)
SOURCE_REVISION_ENV = "APP_VERSION"
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

LEGACY_OAUTH_ENV_NAMES = {
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
}
ADS_CREDENTIAL_ENV_NAMES = {
    *JOB_SECRET_BINDINGS,
    *OPTIONAL_JOB_SECRET_BINDINGS,
    *LEGACY_OAUTH_ENV_NAMES,
    "GOOGLE_APPLICATION_CREDENTIALS",
}
BROAD_PROJECT_ROLES = {"roles/editor", "roles/owner"}
SECRET_ACCESSOR_ROLE = "roles/secretmanager.secretAccessor"  # pragma: allowlist secret
REQUIRED_PROJECT_ROLES = {"roles/datastore.user"}

RUNTIME_NAMES = (
    "GA4_MEASUREMENT_ID",
    "GTM_CONTAINER_ID",
)


def _call(command: list[str], runner: Callable) -> tuple[bool, str]:
    completed = runner(command, capture_output=True, text=True, check=False)
    return completed.returncode == 0, completed.stdout if completed.returncode == 0 else ""


def _runtime_env_items(payload) -> list[dict]:
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
    return [item for item in payload if isinstance(item, dict)]


def _runtime_env_names(payload) -> set[str]:
    return {
        item["name"] for item in _runtime_env_items(payload) if isinstance(item.get("name"), str)
    }


def _runtime_secret_names(payload) -> set[str]:
    secret_names = set()
    for item in _runtime_env_items(payload):
        value_source = item.get("valueSource") or item.get("valueFrom") or {}
        secret_ref = value_source.get("secretKeyRef", {}) if isinstance(value_source, dict) else {}
        if not isinstance(secret_ref, dict):
            continue
        secret_name = secret_ref.get("secret") or secret_ref.get("name")
        if isinstance(secret_name, str):
            secret_names.add(secret_name)
    return secret_names


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


def _iam_roles_for_member(payload, member: str) -> set[str]:
    """Return role names for one member without retaining any other IAM data."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bindings", []), list):
        raise ValueError("unexpected IAM policy shape")
    roles: set[str] = set()
    for binding in payload.get("bindings", []):
        if not isinstance(binding, dict):
            raise ValueError("unexpected IAM binding shape")
        role = binding.get("role")
        members = binding.get("members", [])
        if not isinstance(members, list):
            raise ValueError("unexpected IAM member shape")
        if isinstance(role, str) and member in members:
            roles.add(role)
    return roles


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

    single_container = len(containers) == 1
    container = containers[0] if single_container else {}
    command = container.get("command", [])
    args = container.get("args", [])
    exact_probe_command = (
        single_container
        and isinstance(command, list)
        and tuple(command) == EXPECTED_JOB_COMMAND
        and isinstance(args, list)
        and tuple(args) == EXPECTED_JOB_ARGS
    )
    env_items = [
        item
        for container in containers
        for item in container.get("env", [])
        if isinstance(container.get("env"), list) and isinstance(item, dict)
    ]
    env_names = {item.get("name") for item in env_items if isinstance(item.get("name"), str)}
    env_name_counts: dict[str, int] = {}
    secret_bindings: dict[str, str] = {}
    plain_env_values: dict[str, str] = {}
    for item in env_items:
        env_name = item.get("name")
        if isinstance(env_name, str):
            env_name_counts[env_name] = env_name_counts.get(env_name, 0) + 1
        value_source = item.get("valueSource") or item.get("valueFrom") or {}
        secret_ref = value_source.get("secretKeyRef", {}) if isinstance(value_source, dict) else {}
        if not isinstance(secret_ref, dict):
            continue
        secret_name = secret_ref.get("secret") or secret_ref.get("name")
        if isinstance(env_name, str) and isinstance(secret_name, str):
            secret_bindings[env_name] = secret_name
        elif isinstance(env_name, str) and isinstance(item.get("value"), str):
            plain_env_values[env_name] = item["value"]

    required_bindings = dict(JOB_SECRET_BINDINGS)
    allowed_bindings = (
        required_bindings,
        {**required_bindings, **OPTIONAL_JOB_SECRET_BINDINGS},
    )
    ads_env_names = ADS_CREDENTIAL_ENV_NAMES.intersection(env_names)
    exact_runtime_env = env_names == {*secret_bindings, SOURCE_REVISION_ENV}
    source_revision_bound = (
        exact_runtime_env
        and env_name_counts.get(SOURCE_REVISION_ENV) == 1
        and bool(SOURCE_REVISION_RE.fullmatch(plain_env_values.get(SOURCE_REVISION_ENV, "")))
    )
    managed_secret_bindings = (
        secret_bindings in allowed_bindings
        and ads_env_names == set(secret_bindings)
        and all(env_name_counts.get(name) == 1 for name in ads_env_names)
        and exact_runtime_env
    )
    legacy_oauth_bindings_absent = LEGACY_OAUTH_ENV_NAMES.isdisjoint(env_names) and set(
        LEGACY_OAUTH_SECRETS
    ).isdisjoint(secret_bindings.values())
    login_customer_id_bound = (
        secret_bindings.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
        == OPTIONAL_JOB_SECRET_BINDINGS["GOOGLE_ADS_LOGIN_CUSTOMER_ID"]
    )
    identity_attached = service_accounts == {expected_service_account}
    live_probe_configured = exact_probe_command
    persistent_key_path_absent = "GOOGLE_APPLICATION_CREDENTIALS" not in env_names
    return {
        "dedicated_identity_attached": identity_attached,
        "exact_probe_command": exact_probe_command,
        "managed_secret_bindings": managed_secret_bindings,
        "legacy_oauth_bindings_absent": legacy_oauth_bindings_absent,
        "login_customer_id_bound": login_customer_id_bound,
        "live_probe_configured": live_probe_configured,
        "persistent_key_path_absent": persistent_key_path_absent,
        "source_revision_bound": source_revision_bound,
        "runtime_ready": (
            identity_attached
            and exact_probe_command
            and managed_secret_bindings
            and legacy_oauth_bindings_absent
            and persistent_key_path_absent
            and source_revision_bound
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

    expected_service_account = f"{ADS_SERVICE_ACCOUNT_ID}@{project}.iam.gserviceaccount.com"
    dedicated_service_account = expected_service_account in service_account_emails
    persistent_user_key_absent = False
    iam_policy_checked = False
    broad_project_roles_absent = False
    project_wide_secret_accessor_absent = False
    firestore_access_present = False
    project_roles_least_privilege = False
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
                "projects",
                "get-iam-policy",
                project,
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                project_roles = _iam_roles_for_member(
                    json.loads(output or "{}"),
                    f"serviceAccount:{expected_service_account}",
                )
                iam_policy_checked = True
                broad_project_roles_absent = BROAD_PROJECT_ROLES.isdisjoint(project_roles)
                project_wide_secret_accessor_absent = SECRET_ACCESSOR_ROLE not in project_roles
                firestore_access_present = REQUIRED_PROJECT_ROLES.issubset(project_roles)
                project_roles_least_privilege = project_roles == REQUIRED_PROJECT_ROLES
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("service_account_iam")
        else:
            errors.append("service_account_iam")

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
        "exact_probe_command": False,
        "managed_secret_bindings": False,
        "legacy_oauth_bindings_absent": False,
        "login_customer_id_bound": False,
        "live_probe_configured": False,
        "persistent_key_path_absent": True,
        "source_revision_bound": False,
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

    required_secret_access_present = False
    required_secret_names = list(JOB_SECRET_BINDINGS.values())
    if job_runtime["login_customer_id_bound"]:
        required_secret_names.extend(OPTIONAL_JOB_SECRET_BINDINGS.values())
    if dedicated_service_account and all(name in secret_names for name in required_secret_names):
        secret_access_results = []
        for secret_name in required_secret_names:
            ok, output = _call(
                [
                    "gcloud",
                    "secrets",
                    "get-iam-policy",
                    secret_name,
                    project_flag,
                    "--format=json",
                ],
                runner,
            )
            if not ok:
                if "secret_iam" not in errors:
                    errors.append("secret_iam")
                secret_access_results.append(False)
                continue
            try:
                roles = _iam_roles_for_member(
                    json.loads(output or "{}"),
                    f"serviceAccount:{expected_service_account}",
                )
                secret_access_results.append(SECRET_ACCESSOR_ROLE in roles)
            except (TypeError, ValueError, json.JSONDecodeError):
                if "secret_iam" not in errors:
                    errors.append("secret_iam")
                secret_access_results.append(False)
        required_secret_access_present = bool(secret_access_results) and all(secret_access_results)

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
    runtime_secret_names = set()
    if ok:
        try:
            runtime_payload = json.loads(output or "[]")
            runtime_names = _runtime_env_names(runtime_payload)
            runtime_secret_names = _runtime_secret_names(runtime_payload)
        except (TypeError, ValueError, json.JSONDecodeError, IndexError):
            errors.append("runtime")
    else:
        errors.append("runtime")

    secret_presence = {name: name in secret_names for name in SECRETS}
    legacy_user_oauth = all(secret_presence[name] for name in LEGACY_OAUTH_SECRETS)
    legacy_oauth_secrets_absent = not any(secret_presence[name] for name in LEGACY_OAUTH_SECRETS)
    ads_account_config = (
        secret_presence["google-ads-developer-token"] and secret_presence["google-ads-customer-id"]
    )
    measurement_exactly_one = sum(name in runtime_names for name in RUNTIME_NAMES) == 1
    storefront_ads_credentials_absent = ADS_CREDENTIAL_ENV_NAMES.isdisjoint(runtime_names) and set(
        SECRETS
    ).isdisjoint(runtime_secret_names)
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
    least_privilege_iam = (
        dedicated_service_account
        and persistent_user_key_absent
        and iam_policy_checked
        and broad_project_roles_absent
        and project_wide_secret_accessor_absent
        and firestore_access_present
        and project_roles_least_privilege
        and required_secret_access_present
    )
    service_account_adc = least_privilege_iam and job_runtime["runtime_ready"]
    ads_auth_path = service_account_adc
    presence_ready = (
        not errors
        and ads_api
        and measurement_apis
        and ads_account_config
        and ads_auth_path
        and legacy_oauth_secrets_absent
        and storefront_ads_credentials_absent
        and measurement_exactly_one
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
            "iam_policy_checked": iam_policy_checked,
            "broad_project_roles_absent": broad_project_roles_absent,
            "project_wide_secret_accessor_absent": project_wide_secret_accessor_absent,
            "firestore_access_present": firestore_access_present,
            "project_roles_least_privilege": project_roles_least_privilege,
            "required_secret_access_present": required_secret_access_present,
            "least_privilege_iam": least_privilege_iam,
        },
        "job": {
            "dedicated_job_present": dedicated_job_present,
            **job_runtime,
        },
        "auth_paths": {
            "service_account_adc": service_account_adc,
            "legacy_user_oauth": legacy_user_oauth,
        },
        "runtime": {
            **{name: name in runtime_names for name in RUNTIME_NAMES},
            "measurement_exactly_one": measurement_exactly_one,
            "ads_credentials_absent": storefront_ads_credentials_absent,
        },
        "readiness": {
            "ads_api": ads_api,
            "seo_api": seo_api,
            "measurement_apis": measurement_apis,
            "business_profile_apis": business_profile_apis,
            "google_ecosystem_apis": google_ecosystem_apis,
            "ads_account_config": ads_account_config,
            "ads_auth_path": ads_auth_path,
            "legacy_oauth_secrets_absent": legacy_oauth_secrets_absent,
            "storefront_ads_credentials_absent": storefront_ads_credentials_absent,
            "least_privilege_iam": least_privilege_iam,
            "measurement": measurement_exactly_one,
            "measurement_exactly_one": measurement_exactly_one,
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
