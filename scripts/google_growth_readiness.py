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
ADS_DISPATCHER_SERVICE_ACCOUNT_ID = "google-growth-dispatcher"
ADS_JOB_ID = "google-growth-control"
ADS_PAUSED_CREATE_JOB_ID = "google-growth-paused-create"
ADS_PAUSED_DISPATCHER_JOB_ID = "google-growth-paused-dispatch"
JOB_SECRET_BINDINGS = {
    "GOOGLE_ADS_DEVELOPER_TOKEN": "google-ads-developer-token",
    "GOOGLE_ADS_CUSTOMER_ID": "google-ads-customer-id",
}
OPTIONAL_JOB_SECRET_BINDINGS = {
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "google-ads-login-customer-id",
}
EXPECTED_JOB_COMMAND = ("python", "scripts/google_ads_access_evidence_job.py")
EXPECTED_PAUSED_CREATE_JOB_COMMAND = ("python", "scripts/google_ads_paused_worker_job.py")
EXPECTED_PAUSED_DISPATCHER_JOB_COMMAND = (
    "python",
    "scripts/google_ads_paused_dispatcher_job.py",
)
EXPECTED_JOB_ARGS: tuple[str, ...] = ()
SOURCE_REVISION_ENV = "APP_VERSION"
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")

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
SAFE_JOB_EXECUTION_ROLES = {
    "roles/run.invoker",
    "roles/run.jobsExecutor",
    "roles/run.viewer",
}
PROJECT_JOB_EXECUTION_ROLES = {
    "roles/run.admin",
    "roles/run.developer",
    "roles/run.invoker",
    "roles/run.jobsExecutor",
    "roles/run.jobsExecutorWithOverrides",
}
PUBLIC_IAM_MEMBERS = {"allUsers", "allAuthenticatedUsers"}
STOREFRONT_JOB_ROLES = {
    "roles/editor",
    "roles/owner",
    "roles/run.admin",
    "roles/run.developer",
    "roles/run.invoker",
    "roles/run.jobsExecutor",
    "roles/run.jobsExecutorWithOverrides",
}
IMPERSONATION_ROLES = {
    "roles/iam.serviceAccountTokenCreator",
    "roles/iam.serviceAccountUser",
    "roles/iam.workloadIdentityUser",
}

RUNTIME_NAMES = (
    "GA4_MEASUREMENT_ID",
    "GTM_CONTAINER_ID",
)

DISPATCHER_RUNTIME_ENV_NAMES = {
    SOURCE_REVISION_ENV,
    "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED",
    "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED",
    "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED",
    "THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION",
    "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT",
    "THO_GOOGLE_ADS_PAUSED_CREATE_REGION",
    "THO_GOOGLE_ADS_PAUSED_CREATE_JOB",
    "THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED",
}


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


def _plain_runtime_value(payload, name: str) -> str | None:
    matches = [
        item.get("value")
        for item in _runtime_env_items(payload)
        if item.get("name") == name and isinstance(item.get("value"), str)
    ]
    return matches[0] if len(matches) == 1 else None


def _job_plain_env_value(payload, name: str) -> str | None:
    matches = [
        item.get("value")
        for value in _nested_values(payload, "containers")
        if isinstance(value, list)
        for container in value
        if isinstance(container, dict) and isinstance(container.get("env", []), list)
        for item in container.get("env", [])
        if isinstance(item, dict)
        and item.get("name") == name
        and isinstance(item.get("value"), str)
    ]
    return matches[0] if len(matches) == 1 else None


def _container_image_digest(payload) -> str | None:
    images = [
        container.get("image")
        for value in _nested_values(payload, "containers")
        if isinstance(value, list)
        for container in value
        if isinstance(container, dict) and isinstance(container.get("image"), str)
    ]
    if len(images) != 1 or not IMAGE_DIGEST_RE.fullmatch(images[0]):
        return None
    return images[0].rsplit("@", 1)[1]


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


def _job_policy_has_only_fixed_protocol_roles(payload) -> bool:
    """Reject override-capable, custom, or unknown job-resource roles."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bindings", []), list):
        raise ValueError("unexpected IAM policy shape")
    for binding in payload.get("bindings", []):
        if not isinstance(binding, dict):
            raise ValueError("unexpected IAM binding shape")
        role = binding.get("role")
        members = binding.get("members", [])
        if not isinstance(role, str) or not isinstance(members, list):
            raise ValueError("unexpected IAM binding shape")
        if PUBLIC_IAM_MEMBERS.intersection(members):
            return False
        if members and role not in SAFE_JOB_EXECUTION_ROLES:
            return False
    return True


def _job_policy_has_single_executor(payload, expected_member: str) -> bool:
    """Require one non-override executor; viewer bindings remain advisory-only."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bindings", []), list):
        raise ValueError("unexpected IAM policy shape")
    executors: set[tuple[str, str]] = set()
    for binding in payload.get("bindings", []):
        if not isinstance(binding, dict):
            raise ValueError("unexpected IAM binding shape")
        role = binding.get("role")
        members = binding.get("members", [])
        if not isinstance(role, str) or not isinstance(members, list):
            raise ValueError("unexpected IAM binding shape")
        if role in {"roles/run.invoker", "roles/run.jobsExecutor"}:
            executors.update((role, member) for member in members if isinstance(member, str))
    return executors == {("roles/run.invoker", expected_member)}


def _policy_has_no_roles(payload, forbidden_roles: set[str]) -> bool:
    """Return a presence-only answer without retaining IAM members."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bindings", []), list):
        raise ValueError("unexpected IAM policy shape")
    for binding in payload.get("bindings", []):
        if not isinstance(binding, dict):
            raise ValueError("unexpected IAM binding shape")
        role = binding.get("role")
        members = binding.get("members", [])
        if not isinstance(role, str) or not isinstance(members, list):
            raise ValueError("unexpected IAM binding shape")
        if members and role in forbidden_roles:
            return False
    return True


def _project_policy_has_no_job_execution_authority(payload) -> bool:
    """Reject known executors and every opaque custom project/org role."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bindings", []), list):
        raise ValueError("unexpected IAM policy shape")
    for binding in payload.get("bindings", []):
        if not isinstance(binding, dict):
            raise ValueError("unexpected IAM binding shape")
        role = binding.get("role")
        members = binding.get("members", [])
        if not isinstance(role, str) or not isinstance(members, list):
            raise ValueError("unexpected IAM binding shape")
        if members and (
            role in PROJECT_JOB_EXECUTION_ROLES or role.startswith(("projects/", "organizations/"))
        ):
            return False
    return True


def _job_runtime(
    payload,
    expected_service_account: str,
    *,
    expected_command: tuple[str, ...] = EXPECTED_JOB_COMMAND,
) -> dict[str, bool]:
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
        and tuple(command) == expected_command
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


def _dispatcher_job_runtime(
    payload,
    expected_service_account: str,
    *,
    project: str,
    region: str,
) -> dict[str, bool]:
    """Validate the fixed, secretless outbox dispatcher job template."""
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
    exact_dispatcher_command = (
        single_container
        and isinstance(command, list)
        and tuple(command) == EXPECTED_PAUSED_DISPATCHER_JOB_COMMAND
        and isinstance(args, list)
        and tuple(args) == EXPECTED_JOB_ARGS
    )
    env_items = container.get("env", []) if single_container else []
    if not isinstance(env_items, list) or not all(isinstance(item, dict) for item in env_items):
        raise ValueError("unexpected Cloud Run Job env shape")
    env_names = {item.get("name") for item in env_items if isinstance(item.get("name"), str)}
    env_name_counts: dict[str, int] = {}
    values: dict[str, str] = {}
    secret_bound = False
    for item in env_items:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        env_name_counts[name] = env_name_counts.get(name, 0) + 1
        value_source = item.get("valueSource") or item.get("valueFrom")
        if value_source:
            secret_bound = True
        value = item.get("value")
        if isinstance(value, str):
            values[name] = value

    revision = values.get(SOURCE_REVISION_ENV, "")
    source_revision_bound = bool(
        SOURCE_REVISION_RE.fullmatch(revision)
        and values.get("THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION") == revision
    )
    exact_values = {
        "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT": project,
        "THO_GOOGLE_ADS_PAUSED_CREATE_REGION": region,
        "THO_GOOGLE_ADS_PAUSED_CREATE_JOB": ADS_PAUSED_CREATE_JOB_ID,
        "THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED": "true",
    }
    fixed_runtime_config = (
        env_names == DISPATCHER_RUNTIME_ENV_NAMES
        and all(env_name_counts.get(name) == 1 for name in DISPATCHER_RUNTIME_ENV_NAMES)
        and all(values.get(name) == value for name, value in exact_values.items())
        and not secret_bound
    )
    ads_secret_bindings_absent = (
        ADS_CREDENTIAL_ENV_NAMES.isdisjoint(env_names)
        and set(SECRETS).isdisjoint(values.values())
        and not secret_bound
    )
    dedicated_identity_attached = service_accounts == {expected_service_account}
    return {
        "dedicated_identity_attached": dedicated_identity_attached,
        "exact_dispatcher_command": exact_dispatcher_command,
        "ads_secret_bindings_absent": ads_secret_bindings_absent,
        "fixed_runtime_config": fixed_runtime_config,
        "source_revision_bound": source_revision_bound,
        "runtime_ready": (
            dedicated_identity_attached
            and exact_dispatcher_command
            and ads_secret_bindings_absent
            and fixed_runtime_config
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
    expected_dispatcher_service_account = (
        f"{ADS_DISPATCHER_SERVICE_ACCOUNT_ID}@{project}.iam.gserviceaccount.com"
    )
    dedicated_service_account = expected_service_account in service_account_emails
    dedicated_dispatcher_service_account = (
        expected_dispatcher_service_account in service_account_emails
    )
    persistent_user_key_absent = False
    iam_policy_checked = False
    broad_project_roles_absent = False
    project_wide_secret_accessor_absent = False
    firestore_access_present = False
    project_roles_least_privilege = False
    project_policy: dict = {}
    service_account_policy: dict = {}
    impersonation_policy_checked = False
    dispatcher_persistent_user_key_absent = False
    dispatcher_project_roles_least_privilege = False
    dispatcher_impersonation_policy_checked = False
    dispatcher_service_account_policy: dict = {}
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
                project_policy = json.loads(output or "{}")
                project_roles = _iam_roles_for_member(
                    project_policy,
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
                "iam",
                "service-accounts",
                "get-iam-policy",
                expected_service_account,
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                service_account_policy = json.loads(output or "{}")
                if not isinstance(service_account_policy.get("bindings", []), list):
                    raise ValueError("unexpected IAM policy shape")
                impersonation_policy_checked = True
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("service_account_policy")
        else:
            errors.append("service_account_policy")

    if dedicated_dispatcher_service_account and iam_policy_checked:
        ok, output = _call(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "list",
                f"--iam-account={expected_dispatcher_service_account}",
                "--managed-by=user",
                project_flag,
                "--format=value(name)",
            ],
            runner,
        )
        dispatcher_persistent_user_key_absent = ok and not output.strip()
        if not ok:
            errors.append("dispatcher_service_account_keys")

        try:
            dispatcher_project_roles = _iam_roles_for_member(
                project_policy,
                f"serviceAccount:{expected_dispatcher_service_account}",
            )
            dispatcher_project_roles_least_privilege = (
                dispatcher_project_roles == REQUIRED_PROJECT_ROLES
            )
        except (TypeError, ValueError):
            errors.append("dispatcher_service_account_iam")

        ok, output = _call(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "get-iam-policy",
                expected_dispatcher_service_account,
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                dispatcher_service_account_policy = json.loads(output or "{}")
                dispatcher_impersonation_policy_checked = _policy_has_no_roles(
                    dispatcher_service_account_policy,
                    IMPERSONATION_ROLES,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("dispatcher_service_account_policy")
        else:
            errors.append("dispatcher_service_account_policy")

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
    paused_create_job_present = ADS_PAUSED_CREATE_JOB_ID in job_names
    paused_dispatcher_job_present = ADS_PAUSED_DISPATCHER_JOB_ID in job_names
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
    job_payload: dict = {}
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
                job_payload = json.loads(output)
                job_runtime = _job_runtime(job_payload, expected_service_account)
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("job_runtime")
        else:
            errors.append("job_runtime")

    paused_create_job_runtime = {
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
    paused_create_job_payload: dict = {}
    if paused_create_job_present:
        ok, output = _call(
            [
                "gcloud",
                "run",
                "jobs",
                "describe",
                ADS_PAUSED_CREATE_JOB_ID,
                f"--region={region}",
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                paused_create_job_payload = json.loads(output)
                paused_create_job_runtime = _job_runtime(
                    paused_create_job_payload,
                    expected_service_account,
                    expected_command=EXPECTED_PAUSED_CREATE_JOB_COMMAND,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("paused_create_job_runtime")
        else:
            errors.append("paused_create_job_runtime")

    paused_dispatcher_runtime = {
        "dedicated_identity_attached": False,
        "exact_dispatcher_command": False,
        "ads_secret_bindings_absent": False,
        "fixed_runtime_config": False,
        "source_revision_bound": False,
        "runtime_ready": False,
    }
    paused_dispatcher_job_payload: dict = {}
    if paused_dispatcher_job_present:
        ok, output = _call(
            [
                "gcloud",
                "run",
                "jobs",
                "describe",
                ADS_PAUSED_DISPATCHER_JOB_ID,
                f"--region={region}",
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                paused_dispatcher_job_payload = json.loads(output)
                paused_dispatcher_runtime = _dispatcher_job_runtime(
                    paused_dispatcher_job_payload,
                    expected_dispatcher_service_account,
                    project=project,
                    region=region,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("paused_dispatcher_job_runtime")
        else:
            errors.append("paused_dispatcher_job_runtime")

    override_capable_bindings_absent = False
    job_iam_checked = False
    job_policy: dict = {}
    if dedicated_job_present:
        ok, output = _call(
            [
                "gcloud",
                "run",
                "jobs",
                "get-iam-policy",
                ADS_JOB_ID,
                f"--region={region}",
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                job_policy = json.loads(output or "{}")
                override_capable_bindings_absent = _job_policy_has_only_fixed_protocol_roles(
                    job_policy
                )
                job_iam_checked = True
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("job_iam")
        else:
            errors.append("job_iam")
    execution_iam_ready = job_iam_checked and override_capable_bindings_absent

    paused_create_override_capable_bindings_absent = False
    paused_create_single_dispatcher = False
    paused_create_job_iam_checked = False
    paused_create_job_policy: dict = {}
    if paused_create_job_present:
        ok, output = _call(
            [
                "gcloud",
                "run",
                "jobs",
                "get-iam-policy",
                ADS_PAUSED_CREATE_JOB_ID,
                f"--region={region}",
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                paused_create_job_policy = json.loads(output or "{}")
                paused_create_override_capable_bindings_absent = (
                    _job_policy_has_only_fixed_protocol_roles(paused_create_job_policy)
                )
                paused_create_single_dispatcher = _job_policy_has_single_executor(
                    paused_create_job_policy,
                    f"serviceAccount:{expected_dispatcher_service_account}",
                )
                paused_create_job_iam_checked = True
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("paused_create_job_iam")
        else:
            errors.append("paused_create_job_iam")
    paused_create_execution_iam_ready = (
        paused_create_job_iam_checked
        and paused_create_override_capable_bindings_absent
        and paused_create_single_dispatcher
    )
    project_job_execution_bindings_absent = False
    if iam_policy_checked:
        try:
            project_job_execution_bindings_absent = _project_policy_has_no_job_execution_authority(
                project_policy
            )
        except (TypeError, ValueError):
            errors.append("project_job_execution_iam")

    paused_dispatcher_override_capable_bindings_absent = False
    paused_dispatcher_job_iam_checked = False
    paused_dispatcher_job_policy: dict = {}
    if paused_dispatcher_job_present:
        ok, output = _call(
            [
                "gcloud",
                "run",
                "jobs",
                "get-iam-policy",
                ADS_PAUSED_DISPATCHER_JOB_ID,
                f"--region={region}",
                project_flag,
                "--format=json",
            ],
            runner,
        )
        if ok:
            try:
                paused_dispatcher_job_policy = json.loads(output or "{}")
                paused_dispatcher_override_capable_bindings_absent = (
                    _job_policy_has_only_fixed_protocol_roles(paused_dispatcher_job_policy)
                )
                paused_dispatcher_job_iam_checked = True
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append("paused_dispatcher_job_iam")
        else:
            errors.append("paused_dispatcher_job_iam")
    paused_dispatcher_execution_iam_ready = (
        paused_dispatcher_job_iam_checked and paused_dispatcher_override_capable_bindings_absent
    )

    dispatcher_paused_create_invocation_only = False
    if (
        job_iam_checked
        and paused_create_job_iam_checked
        and paused_dispatcher_job_iam_checked
        and dedicated_dispatcher_service_account
    ):
        dispatcher_member = f"serviceAccount:{expected_dispatcher_service_account}"
        dispatcher_paused_create_invocation_only = (
            _iam_roles_for_member(paused_create_job_policy, dispatcher_member)
            == {"roles/run.invoker"}
            and not _iam_roles_for_member(job_policy, dispatcher_member)
            and not _iam_roles_for_member(paused_dispatcher_job_policy, dispatcher_member)
        )

    required_secret_access_present = False
    ads_secret_policies: dict[str, dict] = {}
    required_secret_names = list(JOB_SECRET_BINDINGS.values())
    if (
        job_runtime["login_customer_id_bound"]
        or paused_create_job_runtime["login_customer_id_bound"]
    ):
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
                secret_policy = json.loads(output or "{}")
                ads_secret_policies[secret_name] = secret_policy
                roles = _iam_roles_for_member(
                    secret_policy,
                    f"serviceAccount:{expected_service_account}",
                )
                secret_access_results.append(SECRET_ACCESSOR_ROLE in roles)
            except (TypeError, ValueError, json.JSONDecodeError):
                if "secret_iam" not in errors:
                    errors.append("secret_iam")
                secret_access_results.append(False)
        required_secret_access_present = bool(secret_access_results) and all(secret_access_results)

    if dedicated_service_account or dedicated_dispatcher_service_account:
        for secret_name in sorted(
            set(SECRETS).intersection(secret_names) - set(ads_secret_policies)
        ):
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
                if "ads_secret_iam" not in errors:
                    errors.append("ads_secret_iam")
                continue
            try:
                policy = json.loads(output or "{}")
                _iam_roles_for_member(policy, f"serviceAccount:{expected_service_account}")
                ads_secret_policies[secret_name] = policy
            except (TypeError, ValueError, json.JSONDecodeError):
                if "ads_secret_iam" not in errors:
                    errors.append("ads_secret_iam")

    present_ads_secret_names = set(SECRETS).intersection(secret_names)
    all_present_ads_secret_policies_checked = set(ads_secret_policies) == present_ads_secret_names

    dispatcher_ads_secret_access_absent = False
    dispatcher_ads_impersonation_absent = False
    if dedicated_dispatcher_service_account and iam_policy_checked:
        dispatcher_member = f"serviceAccount:{expected_dispatcher_service_account}"
        try:
            dispatcher_project_roles = _iam_roles_for_member(
                project_policy,
                dispatcher_member,
            )
            dispatcher_ads_secret_access_absent = (
                all_present_ads_secret_policies_checked
                and SECRET_ACCESSOR_ROLE not in dispatcher_project_roles
                and all(
                    not _iam_roles_for_member(policy, dispatcher_member)
                    for policy in ads_secret_policies.values()
                )
            )
            dispatcher_ads_impersonation_absent = (
                impersonation_policy_checked
                and not _iam_roles_for_member(service_account_policy, dispatcher_member)
                and IMPERSONATION_ROLES.isdisjoint(dispatcher_project_roles)
                and not any(
                    role.startswith(("projects/", "organizations/"))
                    for role in dispatcher_project_roles
                )
            )
        except (TypeError, ValueError):
            errors.append("dispatcher_authority_separation")

    ok, output = _call(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            service,
            f"--region={region}",
            project_flag,
            "--format=json(spec.template.spec.serviceAccountName,spec.template.spec.containers[0].image,spec.template.spec.containers[0].env)",
        ],
        runner,
    )
    runtime_names = set()
    runtime_secret_names = set()
    storefront_identity_separated = False
    storefront_job_invocation_absent = False
    storefront_secret_access_absent = False
    storefront_impersonation_absent = False
    runtime_payload: dict | list = {}
    if ok:
        try:
            runtime_payload = json.loads(output or "[]")
            runtime_names = _runtime_env_names(runtime_payload)
            runtime_secret_names = _runtime_secret_names(runtime_payload)
            storefront_identities = {
                value
                for key in ("serviceAccount", "serviceAccountName")
                for value in _nested_values(runtime_payload, key)
                if isinstance(value, str)
            }
            storefront_identity_separated = (
                len(storefront_identities) == 1
                and expected_service_account not in storefront_identities
                and expected_dispatcher_service_account not in storefront_identities
            )
            if storefront_identity_separated and iam_policy_checked:
                storefront_identity = next(iter(storefront_identities))
                storefront_project_roles = _iam_roles_for_member(
                    project_policy,
                    f"serviceAccount:{storefront_identity}",
                )
                storefront_resource_roles = _iam_roles_for_member(
                    job_policy,
                    f"serviceAccount:{storefront_identity}",
                )
                storefront_paused_create_roles = _iam_roles_for_member(
                    paused_create_job_policy,
                    f"serviceAccount:{storefront_identity}",
                )
                storefront_dispatcher_roles = _iam_roles_for_member(
                    paused_dispatcher_job_policy,
                    f"serviceAccount:{storefront_identity}",
                )
                storefront_roles = (
                    storefront_project_roles
                    | storefront_resource_roles
                    | storefront_paused_create_roles
                    | storefront_dispatcher_roles
                )
                storefront_job_invocation_absent = STOREFRONT_JOB_ROLES.isdisjoint(
                    storefront_roles
                ) and not any(
                    role.startswith(("projects/", "organizations/")) for role in storefront_roles
                )
                storefront_member = f"serviceAccount:{storefront_identity}"
                storefront_secret_access_absent = (
                    all_present_ads_secret_policies_checked
                    and SECRET_ACCESSOR_ROLE not in storefront_project_roles
                    and all(
                        not _iam_roles_for_member(policy, storefront_member)
                        for policy in ads_secret_policies.values()
                    )
                )
                storefront_impersonation_roles = _iam_roles_for_member(
                    service_account_policy,
                    storefront_member,
                )
                storefront_dispatcher_impersonation_roles = _iam_roles_for_member(
                    dispatcher_service_account_policy,
                    storefront_member,
                )
                storefront_impersonation_absent = (
                    impersonation_policy_checked
                    and dispatcher_impersonation_policy_checked
                    and not storefront_impersonation_roles
                    and not storefront_dispatcher_impersonation_roles
                    and IMPERSONATION_ROLES.isdisjoint(storefront_project_roles)
                    and not any(
                        role.startswith(("projects/", "organizations/"))
                        for role in storefront_project_roles
                    )
                )
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
    revision_values = {
        _job_plain_env_value(job_payload, SOURCE_REVISION_ENV),
        _job_plain_env_value(paused_create_job_payload, SOURCE_REVISION_ENV),
        _job_plain_env_value(paused_dispatcher_job_payload, SOURCE_REVISION_ENV),
        _plain_runtime_value(runtime_payload, SOURCE_REVISION_ENV),
    }
    image_digests = {
        _container_image_digest(job_payload),
        _container_image_digest(paused_create_job_payload),
        _container_image_digest(paused_dispatcher_job_payload),
        _container_image_digest(runtime_payload),
    }
    exact_runtime_revision = (
        len(revision_values) == 1
        and None not in revision_values
        and bool(SOURCE_REVISION_RE.fullmatch(next(iter(revision_values))))
    )
    immutable_image_consistent = len(image_digests) == 1 and None not in image_digests
    exact_candidate_runtime = exact_runtime_revision and immutable_image_consistent
    least_privilege_iam = (
        dedicated_service_account
        and persistent_user_key_absent
        and iam_policy_checked
        and broad_project_roles_absent
        and project_wide_secret_accessor_absent
        and firestore_access_present
        and project_roles_least_privilege
        and required_secret_access_present
        and impersonation_policy_checked
    )
    service_account_adc = (
        least_privilege_iam and job_runtime["runtime_ready"] and execution_iam_ready
    )
    job_secret_topology_consistent = (
        job_runtime["login_customer_id_bound"]
        == paused_create_job_runtime["login_customer_id_bound"]
    )
    paused_create_path = (
        least_privilege_iam
        and paused_create_job_runtime["runtime_ready"]
        and paused_create_execution_iam_ready
        and project_job_execution_bindings_absent
        and job_secret_topology_consistent
        and exact_candidate_runtime
    )
    paused_dispatcher_path = (
        dedicated_dispatcher_service_account
        and dispatcher_persistent_user_key_absent
        and dispatcher_project_roles_least_privilege
        and dispatcher_impersonation_policy_checked
        and paused_dispatcher_runtime["runtime_ready"]
        and paused_dispatcher_execution_iam_ready
        and dispatcher_paused_create_invocation_only
        and dispatcher_ads_secret_access_absent
        and dispatcher_ads_impersonation_absent
        and exact_candidate_runtime
    )
    ads_auth_path = service_account_adc
    presence_ready = (
        not errors
        and ads_api
        and measurement_apis
        and ads_account_config
        and ads_auth_path
        and paused_create_path
        and paused_dispatcher_path
        and legacy_oauth_secrets_absent
        and storefront_ads_credentials_absent
        and storefront_secret_access_absent
        and storefront_impersonation_absent
        and storefront_identity_separated
        and storefront_job_invocation_absent
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
            "impersonation_policy_checked": impersonation_policy_checked,
            "least_privilege_iam": least_privilege_iam,
        },
        "job": {
            "dedicated_job_present": dedicated_job_present,
            **job_runtime,
            "override_capable_bindings_absent": override_capable_bindings_absent,
            "execution_iam_ready": execution_iam_ready,
        },
        "paused_create_job": {
            "dedicated_job_present": paused_create_job_present,
            "dedicated_identity_attached": paused_create_job_runtime["dedicated_identity_attached"],
            "exact_paused_create_command": paused_create_job_runtime["exact_probe_command"],
            "managed_secret_bindings": paused_create_job_runtime["managed_secret_bindings"],
            "legacy_oauth_bindings_absent": paused_create_job_runtime[
                "legacy_oauth_bindings_absent"
            ],
            "login_customer_id_bound": paused_create_job_runtime["login_customer_id_bound"],
            "paused_create_configured": paused_create_job_runtime["live_probe_configured"],
            "persistent_key_path_absent": paused_create_job_runtime["persistent_key_path_absent"],
            "source_revision_bound": paused_create_job_runtime["source_revision_bound"],
            "runtime_ready": paused_create_job_runtime["runtime_ready"],
            "override_capable_bindings_absent": (paused_create_override_capable_bindings_absent),
            "execution_iam_ready": paused_create_execution_iam_ready,
            "project_job_execution_bindings_absent": project_job_execution_bindings_absent,
        },
        "paused_dispatcher": {
            "dedicated_identity_present": dedicated_dispatcher_service_account,
            "persistent_user_key_absent": dispatcher_persistent_user_key_absent,
            "project_roles_least_privilege": dispatcher_project_roles_least_privilege,
            "impersonation_policy_checked": dispatcher_impersonation_policy_checked,
            "dedicated_job_present": paused_dispatcher_job_present,
            **paused_dispatcher_runtime,
            "override_capable_bindings_absent": (
                paused_dispatcher_override_capable_bindings_absent
            ),
            "execution_iam_ready": paused_dispatcher_execution_iam_ready,
            "paused_create_invocation_only": dispatcher_paused_create_invocation_only,
            "ads_secret_access_absent": dispatcher_ads_secret_access_absent,
            "ads_impersonation_absent": dispatcher_ads_impersonation_absent,
            "ready": paused_dispatcher_path,
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
            "paused_create_path": paused_create_path,
            "paused_dispatcher_path": paused_dispatcher_path,
            "exact_runtime_revision": exact_runtime_revision,
            "immutable_image_consistent": immutable_image_consistent,
            "legacy_oauth_secrets_absent": legacy_oauth_secrets_absent,
            "storefront_ads_credentials_absent": storefront_ads_credentials_absent,
            "storefront_secret_access_absent": storefront_secret_access_absent,
            "storefront_impersonation_absent": storefront_impersonation_absent,
            "storefront_identity_separated": storefront_identity_separated,
            "storefront_job_invocation_absent": storefront_job_invocation_absent,
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
