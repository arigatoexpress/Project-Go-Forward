import json
from types import SimpleNamespace

import pytest

import scripts.google_growth_readiness as readiness

PROJECT = "tho-ai-agent"
SERVICE_ACCOUNT = "google-growth-control@tho-ai-agent.iam.gserviceaccount.com"
SERVICE_ACCOUNT_MEMBER = f"serviceAccount:{SERVICE_ACCOUNT}"


def _runner(responses):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        matches = [
            (key, output) for key, output in responses.items() if tuple(command[: len(key)]) == key
        ]
        key, output = max(matches, key=lambda item: len(item[0]))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    return run, calls


def _job_payload(
    *,
    service_account=SERVICE_ACCOUNT,
    command=None,
    args=None,
    env=None,
):
    if command is None:
        command = ["python"]
    if args is None:
        args = ["scripts/google_ads_access_evidence_job.py"]
    if env is None:
        env = [
            {
                "name": "GOOGLE_ADS_DEVELOPER_TOKEN",
                "valueFrom": {"secretKeyRef": {"name": "google-ads-developer-token"}},
            },
            {
                "name": "GOOGLE_ADS_CUSTOMER_ID",
                "valueFrom": {"secretKeyRef": {"name": "google-ads-customer-id"}},
            },
            {"name": "APP_VERSION", "value": "a" * 40},
        ]
    return {
        "spec": {
            "template": {
                "spec": {
                    "template": {
                        "spec": {
                            "serviceAccountName": service_account,
                            "containers": [{"command": command, "args": args, "env": env}],
                        }
                    }
                }
            }
        }
    }


def _iam_policy(*roles):
    return json.dumps(
        {"bindings": [{"role": role, "members": [SERVICE_ACCOUNT_MEMBER]} for role in roles]}
    )


def _secret_policy():
    return _iam_policy("roles/secretmanager.secretAccessor")


def _healthy_responses(*, enabled_services=None, runtime_env=None, job_payload=None):
    if enabled_services is None:
        enabled_services = readiness.SERVICES
    if runtime_env is None:
        runtime_env = [{"name": "GTM_CONTAINER_ID", "value": "GTM-DO-NOT-LEAK"}]
    return {
        ("gcloud", "services", "list"): "\n".join(enabled_services),
        ("gcloud", "secrets", "list"): ("google-ads-developer-token\ngoogle-ads-customer-id\n"),
        ("gcloud", "iam", "service-accounts"): f"{SERVICE_ACCOUNT}\n",
        ("gcloud", "iam", "service-accounts", "keys"): "",
        ("gcloud", "projects", "get-iam-policy"): _iam_policy("roles/datastore.user"),
        (
            "gcloud",
            "secrets",
            "get-iam-policy",
            "google-ads-developer-token",
        ): _secret_policy(),
        (
            "gcloud",
            "secrets",
            "get-iam-policy",
            "google-ads-customer-id",
        ): _secret_policy(),
        ("gcloud", "run", "jobs", "list"): "google-growth-control\n",
        ("gcloud", "run", "jobs", "describe"): json.dumps(job_payload or _job_payload()),
        ("gcloud", "run", "services"): json.dumps(runtime_env),
    }


def test_audit_reports_only_presence_not_secret_values():
    run, calls = _runner(
        {
            (
                "gcloud",
                "services",
                "list",
            ): "googleads.googleapis.com\nsearchconsole.googleapis.com\n",
            ("gcloud", "secrets", "list"): "google-ads-developer-token\ngoogle-ads-refresh-token\n",
            (
                "gcloud",
                "iam",
                "service-accounts",
            ): "691674245427-compute@developer.gserviceaccount.com\n",
            ("gcloud", "run", "jobs", "list"): "",
            ("gcloud", "run", "services"): json.dumps(
                [
                    {"name": "GA4_MEASUREMENT_ID", "value": "G-SECRETISH123"},
                    {
                        "name": "ADMIN_SESSION_SECRET",
                        "valueFrom": {"secretKeyRef": {"name": "admin"}},
                    },
                ]
            ),
        }
    )

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["services"]["googleads.googleapis.com"] is True
    assert result["services"]["analyticsdata.googleapis.com"] is False
    assert result["secrets"]["google-ads-developer-token"] is True
    assert result["secrets"]["google-ads-client-secret"] is False
    assert result["service_account"]["dedicated_identity_present"] is False
    assert result["runtime"]["GA4_MEASUREMENT_ID"] is True
    assert result["readiness"]["ads_auth_path"] is False
    assert "G-SECRETISH123" not in json.dumps(result)
    assert all("--project=tho-ai-agent" in call for call in calls)


def test_audit_degrades_to_error_without_leaking_stderr():
    def fail(_command, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="token=do-not-print")

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=fail)
    assert result["errors"] == [
        "services",
        "secrets",
        "service_accounts",
        "jobs",
        "runtime",
    ]
    assert "do-not-print" not in json.dumps(result)


def test_runtime_parser_accepts_current_gcloud_wrapped_shape():
    payload = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "env": [
                                {"name": "GA4_MEASUREMENT_ID", "value": "redacted"},
                                {"name": "OTHER", "valueFrom": {"secretKeyRef": {"name": "x"}}},
                            ]
                        }
                    ]
                }
            }
        }
    }
    assert readiness._runtime_env_names(payload) == {"GA4_MEASUREMENT_ID", "OTHER"}


def test_dedicated_service_account_is_preferred_without_legacy_oauth_secrets():
    run, _calls = _runner(_healthy_responses())

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"] == {
        "dedicated_identity_present": True,
        "persistent_user_key_absent": True,
        "iam_policy_checked": True,
        "broad_project_roles_absent": True,
        "project_wide_secret_accessor_absent": True,
        "firestore_access_present": True,
        "project_roles_least_privilege": True,
        "required_secret_access_present": True,
        "least_privilege_iam": True,
    }
    assert result["job"] == {
        "dedicated_job_present": True,
        "dedicated_identity_attached": True,
        "exact_probe_command": True,
        "managed_secret_bindings": True,
        "legacy_oauth_bindings_absent": True,
        "login_customer_id_bound": False,
        "live_probe_configured": True,
        "persistent_key_path_absent": True,
        "source_revision_bound": True,
        "runtime_ready": True,
    }
    assert result["auth_paths"] == {
        "service_account_adc": True,
        "legacy_user_oauth": False,
    }
    assert result["readiness"] == {
        "ads_api": True,
        "seo_api": True,
        "measurement_apis": True,
        "business_profile_apis": True,
        "google_ecosystem_apis": True,
        "ads_account_config": True,
        "ads_auth_path": True,
        "legacy_oauth_secrets_absent": True,
        "storefront_ads_credentials_absent": True,
        "least_privilege_iam": True,
        "measurement": True,
        "measurement_exactly_one": True,
        "presence_ready": True,
        "account_access_validated": False,
        "ready_to_spend": False,
    }
    assert "GTM-DO-NOT-LEAK" not in json.dumps(result)


def test_legacy_user_oauth_is_reported_but_does_not_satisfy_gcp_native_strict_mode():
    legacy_secrets = (
        "google-ads-developer-token\n"
        "google-ads-customer-id\n"
        "google-ads-client-id\n"
        "google-ads-client-secret\n"
        "google-ads-refresh-token\n"
    )
    run, _calls = _runner(
        {
            ("gcloud", "services", "list"): "\n".join(readiness.SERVICES),
            ("gcloud", "secrets", "list"): legacy_secrets,
            ("gcloud", "iam", "service-accounts"): "",
            ("gcloud", "run", "jobs", "list"): "",
            ("gcloud", "run", "services"): json.dumps(
                [{"name": "GA4_MEASUREMENT_ID", "value": "G-DO-NOT-LEAK"}]
            ),
        }
    )

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["auth_paths"]["service_account_adc"] is False
    assert result["auth_paths"]["legacy_user_oauth"] is True
    assert result["readiness"]["presence_ready"] is False


def test_default_compute_identity_never_counts_as_ads_auth_path():
    run, _calls = _runner(
        {
            ("gcloud", "services", "list"): "\n".join(readiness.SERVICES),
            ("gcloud", "secrets", "list"): ("google-ads-developer-token\ngoogle-ads-customer-id\n"),
            ("gcloud", "iam", "service-accounts"): (
                "691674245427-compute@developer.gserviceaccount.com\n"
            ),
            ("gcloud", "run", "jobs", "list"): "",
            ("gcloud", "run", "services"): json.dumps(
                [{"name": "GA4_MEASUREMENT_ID", "value": "redacted"}]
            ),
        }
    )

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"]["dedicated_identity_present"] is False
    assert result["auth_paths"]["service_account_adc"] is False
    assert result["readiness"]["presence_ready"] is False


def test_job_runtime_rejects_wrong_identity_missing_live_flag_or_plaintext_secret():
    expected = SERVICE_ACCOUNT

    wrong_identity = readiness._job_runtime(
        _job_payload(service_account="691674245427-compute@developer.gserviceaccount.com"),
        expected,
    )
    missing_live = readiness._job_runtime(_job_payload(args=["probe.py"]), expected)
    plaintext_token = readiness._job_runtime(
        _job_payload(
            env=[
                {"name": "GOOGLE_ADS_DEVELOPER_TOKEN", "value": "unsafe"},
                {
                    "name": "GOOGLE_ADS_CUSTOMER_ID",
                    "valueFrom": {"secretKeyRef": {"name": "google-ads-customer-id"}},
                },
            ]
        ),
        expected,
    )

    assert wrong_identity["runtime_ready"] is False
    assert missing_live["runtime_ready"] is False
    assert plaintext_token["managed_secret_bindings"] is False
    assert plaintext_token["runtime_ready"] is False


def test_job_runtime_requires_exact_probe_command_without_override_surface():
    valid = readiness._job_runtime(_job_payload(), SERVICE_ACCOUNT)
    shell = readiness._job_runtime(
        _job_payload(
            command=["sh", "-c"], args=["python scripts/google_ads_access_evidence_job.py"]
        ),
        SERVICE_ACCOUNT,
    )
    extra_argument = readiness._job_runtime(
        _job_payload(args=["scripts/google_ads_access_evidence_job.py", "--customer-id=override"]),
        SERVICE_ACCOUNT,
    )
    sidecar = _job_payload()
    sidecar["spec"]["template"]["spec"]["template"]["spec"]["containers"].append(
        {"command": ["python"], "args": ["other.py"], "env": []}
    )

    assert valid["exact_probe_command"] is True
    assert valid["runtime_ready"] is True
    for unsafe in (shell, extra_argument, readiness._job_runtime(sidecar, SERVICE_ACCOUNT)):
        assert unsafe["exact_probe_command"] is False
        assert unsafe["runtime_ready"] is False


def test_job_runtime_requires_pinned_source_revision_and_rejects_arbitrary_env_overrides():
    required = _job_payload()["spec"]["template"]["spec"]["template"]["spec"]["containers"][0][
        "env"
    ]
    missing_revision = readiness._job_runtime(
        _job_payload(env=[item for item in required if item["name"] != "APP_VERSION"]),
        SERVICE_ACCOUNT,
    )
    invalid_revision = readiness._job_runtime(
        _job_payload(
            env=[
                *[item for item in required if item["name"] != "APP_VERSION"],
                {"name": "APP_VERSION", "value": "LATEST"},
            ]
        ),
        SERVICE_ACCOUNT,
    )
    arbitrary_override = readiness._job_runtime(
        _job_payload(env=[*required, {"name": "DEPLOYMENT_ID", "value": "override"}]),
        SERVICE_ACCOUNT,
    )

    for unsafe in (missing_revision, invalid_revision, arbitrary_override):
        assert unsafe["source_revision_bound"] is False
        assert unsafe["runtime_ready"] is False


def test_job_runtime_allows_optional_login_binding_but_rejects_legacy_or_extra_secrets():
    required = _job_payload()["spec"]["template"]["spec"]["template"]["spec"]["containers"][0][
        "env"
    ]
    with_login = readiness._job_runtime(
        _job_payload(
            env=[
                *required,
                {
                    "name": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
                    "valueFrom": {"secretKeyRef": {"name": "google-ads-login-customer-id"}},
                },
            ]
        ),
        SERVICE_ACCOUNT,
    )
    with_legacy = readiness._job_runtime(
        _job_payload(
            env=[
                *required,
                {
                    "name": "GOOGLE_ADS_REFRESH_TOKEN",
                    "valueFrom": {"secretKeyRef": {"name": "google-ads-refresh-token"}},
                },
            ]
        ),
        SERVICE_ACCOUNT,
    )
    with_extra_secret = readiness._job_runtime(
        _job_payload(
            env=[
                *required,
                {
                    "name": "UNRELATED_SECRET",
                    "valueFrom": {"secretKeyRef": {"name": "unrelated-secret"}},
                },
            ]
        ),
        SERVICE_ACCOUNT,
    )

    assert with_login["managed_secret_bindings"] is True
    assert with_login["login_customer_id_bound"] is True
    assert with_login["runtime_ready"] is True
    for unsafe in (with_legacy, with_extra_secret):
        assert unsafe["managed_secret_bindings"] is False
        assert unsafe["runtime_ready"] is False


def test_storefront_ads_credentials_block_presence_readiness_without_exposing_values():
    runtime_env = [
        {"name": "GTM_CONTAINER_ID", "value": "GTM-DO-NOT-LEAK"},
        {
            "name": "GOOGLE_ADS_DEVELOPER_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": "google-ads-developer-token"}},
        },
    ]
    run, _calls = _runner(_healthy_responses(runtime_env=runtime_env))

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["storefront_ads_credentials_absent"] is False
    assert result["readiness"]["presence_ready"] is False
    assert "GTM-DO-NOT-LEAK" not in json.dumps(result)


def test_storefront_ads_secret_alias_blocks_presence_readiness():
    runtime_env = [
        {"name": "GTM_CONTAINER_ID", "value": "GTM-DO-NOT-LEAK"},
        {
            "name": "UNRELATED_ALIAS",
            "valueFrom": {"secretKeyRef": {"name": "google-ads-developer-token"}},
        },
    ]
    run, _calls = _runner(_healthy_responses(runtime_env=runtime_env))

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["runtime"]["ads_credentials_absent"] is False
    assert result["readiness"]["storefront_ads_credentials_absent"] is False
    assert result["readiness"]["presence_ready"] is False


@pytest.mark.parametrize(
    ("runtime_env", "expected"),
    [
        ([], False),
        ([{"name": "GA4_MEASUREMENT_ID", "value": "redacted"}], True),
        ([{"name": "GTM_CONTAINER_ID", "value": "redacted"}], True),
        (
            [
                {"name": "GA4_MEASUREMENT_ID", "value": "redacted"},
                {"name": "GTM_CONTAINER_ID", "value": "redacted"},
            ],
            False,
        ),
    ],
)
def test_measurement_requires_exactly_one_loader(runtime_env, expected):
    run, _calls = _runner(_healthy_responses(runtime_env=runtime_env))

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["measurement_exactly_one"] is expected
    assert result["readiness"]["presence_ready"] is expected


def test_advisory_search_console_and_business_profile_apis_do_not_block_ads_presence():
    required_services = (
        "googleads.googleapis.com",
        "analyticsadmin.googleapis.com",
        "analyticsdata.googleapis.com",
    )
    run, _calls = _runner(_healthy_responses(enabled_services=required_services))

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["ads_api"] is True
    assert result["readiness"]["measurement_apis"] is True
    assert result["readiness"]["seo_api"] is False
    assert result["readiness"]["business_profile_apis"] is False
    assert result["readiness"]["google_ecosystem_apis"] is False
    assert result["readiness"]["presence_ready"] is True


@pytest.mark.parametrize("broad_role", ["roles/editor", "roles/owner"])
def test_broad_project_role_blocks_least_privilege_auth_path(broad_role):
    responses = _healthy_responses()
    responses[("gcloud", "projects", "get-iam-policy")] = _iam_policy(broad_role)
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["service_account"]["iam_policy_checked"] is True
    assert result["service_account"]["broad_project_roles_absent"] is False
    assert result["service_account"]["least_privilege_iam"] is False
    assert result["readiness"]["ads_auth_path"] is False
    assert result["readiness"]["presence_ready"] is False
    assert broad_role not in json.dumps(result)


def test_project_wide_secret_accessor_or_missing_resource_access_blocks_auth_path():
    broad = _healthy_responses()
    broad[("gcloud", "projects", "get-iam-policy")] = _iam_policy(
        "roles/datastore.user", "roles/secretmanager.secretAccessor"
    )
    broad_run, _calls = _runner(broad)
    broad_result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=broad_run)

    missing = _healthy_responses()
    missing[("gcloud", "secrets", "get-iam-policy", "google-ads-customer-id")] = _iam_policy()
    missing_run, _calls = _runner(missing)
    missing_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=missing_run
    )

    assert broad_result["service_account"]["project_wide_secret_accessor_absent"] is False
    assert broad_result["service_account"]["least_privilege_iam"] is False
    assert missing_result["service_account"]["required_secret_access_present"] is False
    assert missing_result["service_account"]["least_privilege_iam"] is False
    assert broad_result["readiness"]["presence_ready"] is False
    assert missing_result["readiness"]["presence_ready"] is False


def test_missing_or_additional_project_role_blocks_direct_firestore_evidence_writer():
    missing = _healthy_responses()
    missing[("gcloud", "projects", "get-iam-policy")] = _iam_policy()
    missing_run, _calls = _runner(missing)
    missing_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=missing_run
    )

    additional = _healthy_responses()
    additional[("gcloud", "projects", "get-iam-policy")] = _iam_policy(
        "roles/datastore.user",
        "roles/viewer",
    )
    additional_run, _calls = _runner(additional)
    additional_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=additional_run
    )

    assert missing_result["service_account"]["firestore_access_present"] is False
    assert missing_result["service_account"]["least_privilege_iam"] is False
    assert additional_result["service_account"]["firestore_access_present"] is True
    assert additional_result["service_account"]["project_roles_least_privilege"] is False
    assert additional_result["service_account"]["least_privilege_iam"] is False


def test_any_legacy_oauth_secret_presence_blocks_preferred_path():
    responses = _healthy_responses()
    responses[("gcloud", "secrets", "list")] += "google-ads-refresh-token\n"
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["auth_paths"]["legacy_user_oauth"] is False
    assert result["readiness"]["legacy_oauth_secrets_absent"] is False
    assert result["readiness"]["presence_ready"] is False


def test_user_managed_service_account_key_blocks_keyless_auth_path():
    responses = _healthy_responses(
        runtime_env=[{"name": "GA4_MEASUREMENT_ID", "value": "redacted"}]
    )
    responses[("gcloud", "iam", "service-accounts", "keys")] = (
        "projects/tho-ai-agent/serviceAccounts/redacted/keys/redacted\n"
    )
    run, _calls = _runner(responses)

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"]["persistent_user_key_absent"] is False
    assert result["job"]["runtime_ready"] is True
    assert result["auth_paths"]["service_account_adc"] is False
    assert "keys/redacted" not in json.dumps(result)


def test_service_account_without_a_runnable_dedicated_job_is_not_an_auth_path():
    responses = _healthy_responses(
        runtime_env=[{"name": "GA4_MEASUREMENT_ID", "value": "redacted"}]
    )
    responses[("gcloud", "run", "jobs", "list")] = ""
    run, _calls = _runner(responses)

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"]["dedicated_identity_present"] is True
    assert result["job"]["dedicated_job_present"] is False
    assert result["auth_paths"]["service_account_adc"] is False
    assert result["readiness"]["presence_ready"] is False
