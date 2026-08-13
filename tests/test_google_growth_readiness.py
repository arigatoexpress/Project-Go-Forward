import json
from types import SimpleNamespace

import pytest

import scripts.google_growth_readiness as readiness

PROJECT = "tho-ai-agent"
SERVICE_ACCOUNT = "google-growth-control@tho-ai-agent.iam.gserviceaccount.com"
SERVICE_ACCOUNT_MEMBER = f"serviceAccount:{SERVICE_ACCOUNT}"
DISPATCHER_SERVICE_ACCOUNT = "google-growth-dispatcher@tho-ai-agent.iam.gserviceaccount.com"
DISPATCHER_SERVICE_ACCOUNT_MEMBER = f"serviceAccount:{DISPATCHER_SERVICE_ACCOUNT}"
STOREFRONT_SERVICE_ACCOUNT = "project-go-forward@tho-ai-agent.iam.gserviceaccount.com"
IMAGE = f"us-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/app@sha256:{'d' * 64}"


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
    script="scripts/google_ads_access_evidence_job.py",
):
    if command is None:
        command = ["python", script]
    if args is None:
        args = []
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
                            "containers": [
                                {"image": IMAGE, "command": command, "args": args, "env": env}
                            ],
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


def _project_iam_policy(*, dispatcher_roles=("roles/datastore.user",)):
    bindings = [
        {"role": "roles/datastore.user", "members": [SERVICE_ACCOUNT_MEMBER]},
    ]
    bindings.extend(
        {"role": role, "members": [DISPATCHER_SERVICE_ACCOUNT_MEMBER]} for role in dispatcher_roles
    )
    return json.dumps({"bindings": bindings})


def _dispatcher_job_payload(*, service_account=DISPATCHER_SERVICE_ACCOUNT, env=None):
    revision = "a" * 40
    if env is None:
        env = [
            {"name": "APP_VERSION", "value": revision},
            {
                "name": "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED",
                "value": "true",
            },
            {
                "name": "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED",
                "value": "true",
            },
            {
                "name": "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED",
                "value": "true",
            },
            {
                "name": "THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION",
                "value": revision,
            },
            {"name": "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT", "value": PROJECT},
            {"name": "THO_GOOGLE_ADS_PAUSED_CREATE_REGION", "value": "us-central1"},
            {
                "name": "THO_GOOGLE_ADS_PAUSED_CREATE_JOB",
                "value": readiness.ADS_PAUSED_CREATE_JOB_ID,
            },
            {
                "name": "THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED",
                "value": "true",
            },
        ]
    return _job_payload(
        service_account=service_account,
        command=["python", "scripts/google_ads_paused_dispatcher_job.py"],
        args=[],
        env=env,
    )


def _secret_policy():
    return _iam_policy("roles/secretmanager.secretAccessor")


def _healthy_responses(
    *,
    enabled_services=None,
    runtime_env=None,
    job_payload=None,
    paused_create_job_payload=None,
    dispatcher_job_payload=None,
):
    if enabled_services is None:
        enabled_services = readiness.SERVICES
    if runtime_env is None:
        runtime_env = [{"name": "GTM_CONTAINER_ID", "value": "GTM-DO-NOT-LEAK"}]
    runtime_env = [*runtime_env, {"name": "APP_VERSION", "value": "a" * 40}]
    return {
        ("gcloud", "services", "list"): "\n".join(enabled_services),
        ("gcloud", "secrets", "list"): ("google-ads-developer-token\ngoogle-ads-customer-id\n"),
        ("gcloud", "iam", "service-accounts"): (
            f"{SERVICE_ACCOUNT}\n{DISPATCHER_SERVICE_ACCOUNT}\n"
        ),
        ("gcloud", "iam", "service-accounts", "keys"): "",
        ("gcloud", "iam", "service-accounts", "get-iam-policy"): json.dumps({"bindings": []}),
        ("gcloud", "projects", "get-iam-policy"): _project_iam_policy(),
        ("gcloud", "secrets", "get-iam-policy"): json.dumps({"bindings": []}),
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
        ("gcloud", "run", "jobs", "list"): (
            "google-growth-control\ngoogle-growth-paused-create\ngoogle-growth-paused-dispatch\n"
        ),
        (
            "gcloud",
            "run",
            "jobs",
            "describe",
            readiness.ADS_JOB_ID,
        ): json.dumps(job_payload or _job_payload()),
        (
            "gcloud",
            "run",
            "jobs",
            "describe",
            readiness.ADS_PAUSED_CREATE_JOB_ID,
        ): json.dumps(
            paused_create_job_payload
            or _job_payload(script="scripts/google_ads_paused_worker_job.py")
        ),
        (
            "gcloud",
            "run",
            "jobs",
            "describe",
            readiness.ADS_PAUSED_DISPATCHER_JOB_ID,
        ): json.dumps(dispatcher_job_payload or _dispatcher_job_payload()),
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_JOB_ID,
        ): json.dumps(
            {
                "bindings": [
                    {
                        "role": "roles/run.invoker",
                        "members": ["user:operator@example.invalid"],
                    }
                ]
            }
        ),
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_PAUSED_DISPATCHER_JOB_ID,
        ): json.dumps(
            {
                "bindings": [
                    {
                        "role": "roles/run.invoker",
                        "members": ["user:operator@example.invalid"],
                    }
                ]
            }
        ),
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_PAUSED_CREATE_JOB_ID,
        ): json.dumps(
            {
                "bindings": [
                    {
                        "role": "roles/run.invoker",
                        "members": [DISPATCHER_SERVICE_ACCOUNT_MEMBER],
                    }
                ]
            }
        ),
        ("gcloud", "run", "services"): json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {
                            "serviceAccountName": STOREFRONT_SERVICE_ACCOUNT,
                            "containers": [{"image": IMAGE, "env": runtime_env}],
                        }
                    }
                }
            }
        ),
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
        "impersonation_policy_checked": True,
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
        "override_capable_bindings_absent": True,
        "execution_iam_ready": True,
    }
    assert result["paused_create_job"] == {
        "dedicated_job_present": True,
        "dedicated_identity_attached": True,
        "exact_paused_create_command": True,
        "managed_secret_bindings": True,
        "legacy_oauth_bindings_absent": True,
        "login_customer_id_bound": False,
        "paused_create_configured": True,
        "persistent_key_path_absent": True,
        "source_revision_bound": True,
        "runtime_ready": True,
        "override_capable_bindings_absent": True,
        "execution_iam_ready": True,
        "project_job_execution_bindings_absent": True,
    }
    assert result["paused_dispatcher"] == {
        "dedicated_identity_present": True,
        "persistent_user_key_absent": True,
        "project_roles_least_privilege": True,
        "impersonation_policy_checked": True,
        "dedicated_job_present": True,
        "dedicated_identity_attached": True,
        "exact_dispatcher_command": True,
        "ads_secret_bindings_absent": True,
        "fixed_runtime_config": True,
        "source_revision_bound": True,
        "runtime_ready": True,
        "override_capable_bindings_absent": True,
        "execution_iam_ready": True,
        "paused_create_invocation_only": True,
        "ads_secret_access_absent": True,
        "ads_impersonation_absent": True,
        "ready": True,
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
        "paused_create_path": True,
        "paused_dispatcher_path": True,
        "exact_runtime_revision": True,
        "immutable_image_consistent": True,
        "legacy_oauth_secrets_absent": True,
        "storefront_ads_credentials_absent": True,
        "storefront_secret_access_absent": True,
        "storefront_impersonation_absent": True,
        "storefront_identity_separated": True,
        "storefront_job_invocation_absent": True,
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
        _job_payload(command=["sh", "-c"], args=["python google_ads_access_evidence_job.py"]),
        SERVICE_ACCOUNT,
    )
    extra_argument = readiness._job_runtime(
        _job_payload(args=["--customer-id=override"]),
        SERVICE_ACCOUNT,
    )
    sidecar = _job_payload()
    sidecar["spec"]["template"]["spec"]["template"]["spec"]["containers"].append(
        {"command": ["python", "other.py"], "args": [], "env": []}
    )

    assert valid["exact_probe_command"] is True
    assert valid["runtime_ready"] is True
    for unsafe in (shell, extra_argument, readiness._job_runtime(sidecar, SERVICE_ACCOUNT)):
        assert unsafe["exact_probe_command"] is False
        assert unsafe["runtime_ready"] is False


def test_paused_create_job_runtime_requires_fixed_zero_argument_entrypoint():
    valid = readiness._job_runtime(
        _job_payload(script="scripts/google_ads_paused_worker_job.py"),
        SERVICE_ACCOUNT,
        expected_command=("python", "scripts/google_ads_paused_worker_job.py"),
    )
    extra_argument = readiness._job_runtime(
        _job_payload(
            script="scripts/google_ads_paused_worker_job.py",
            args=["--deployment-id=override"],
        ),
        SERVICE_ACCOUNT,
        expected_command=("python", "scripts/google_ads_paused_worker_job.py"),
    )
    replaceable_script = readiness._job_runtime(
        _job_payload(
            command=["python"],
            args=["scripts/google_ads_paused_worker_job.py"],
        ),
        SERVICE_ACCOUNT,
        expected_command=("python", "scripts/google_ads_paused_worker_job.py"),
    )

    assert valid["exact_probe_command"] is True
    assert valid["runtime_ready"] is True
    for unsafe in (extra_argument, replaceable_script):
        assert unsafe["exact_probe_command"] is False
        assert unsafe["runtime_ready"] is False


def test_paused_dispatcher_requires_separate_fixed_job_with_no_ads_secrets():
    valid = readiness._dispatcher_job_runtime(
        _dispatcher_job_payload(),
        DISPATCHER_SERVICE_ACCOUNT,
        project=PROJECT,
        region="us-central1",
    )
    ads_secret = _dispatcher_job_payload()
    env = ads_secret["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]
    env.append(
        {
            "name": "GOOGLE_ADS_CUSTOMER_ID",
            "valueFrom": {"secretKeyRef": {"name": "google-ads-customer-id"}},
        }
    )
    wrong_identity = readiness._dispatcher_job_runtime(
        _dispatcher_job_payload(service_account=SERVICE_ACCOUNT),
        DISPATCHER_SERVICE_ACCOUNT,
        project=PROJECT,
        region="us-central1",
    )
    replaceable_script = _dispatcher_job_payload()
    container = replaceable_script["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]
    container["command"] = ["python"]
    container["args"] = ["scripts/google_ads_paused_dispatcher_job.py"]

    assert valid == {
        "dedicated_identity_attached": True,
        "exact_dispatcher_command": True,
        "ads_secret_bindings_absent": True,
        "fixed_runtime_config": True,
        "source_revision_bound": True,
        "runtime_ready": True,
    }
    for unsafe in (
        readiness._dispatcher_job_runtime(
            ads_secret,
            DISPATCHER_SERVICE_ACCOUNT,
            project=PROJECT,
            region="us-central1",
        ),
        wrong_identity,
        readiness._dispatcher_job_runtime(
            replaceable_script,
            DISPATCHER_SERVICE_ACCOUNT,
            project=PROJECT,
            region="us-central1",
        ),
    ):
        assert unsafe["runtime_ready"] is False


@pytest.mark.parametrize(
    ("mutation", "expected_gate"),
    [
        ({"THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED": "TRUE"}, "fixed_runtime_config"),
        ({"THO_GOOGLE_ADS_PAUSED_CREATE_JOB": "another-job"}, "fixed_runtime_config"),
        ({"THO_GOOGLE_ADS_PAUSED_CREATE_REGION": "europe-west1"}, "fixed_runtime_config"),
        ({"APP_VERSION": "latest"}, "source_revision_bound"),
    ],
)
def test_paused_dispatcher_runtime_rejects_unverified_or_unbound_config(mutation, expected_gate):
    payload = _dispatcher_job_payload()
    env = payload["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]
    for item in env:
        if item["name"] in mutation:
            item["value"] = mutation[item["name"]]

    result = readiness._dispatcher_job_runtime(
        payload,
        DISPATCHER_SERVICE_ACCOUNT,
        project=PROJECT,
        region="us-central1",
    )

    assert result[expected_gate] is False
    assert result["runtime_ready"] is False


def test_dispatcher_identity_must_be_keyless_firestore_only_and_single_job_invoker():
    broad = _healthy_responses()
    broad[("gcloud", "projects", "get-iam-policy")] = _project_iam_policy(
        dispatcher_roles=("roles/datastore.user", "roles/editor")
    )
    broad_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(broad)[0]
    )

    wrong_target = _healthy_responses()
    wrong_target[
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_JOB_ID,
        )
    ] = json.dumps(
        {
            "bindings": [
                {
                    "role": "roles/run.invoker",
                    "members": [DISPATCHER_SERVICE_ACCOUNT_MEMBER],
                }
            ]
        }
    )
    wrong_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(wrong_target)[0]
    )

    assert broad_result["paused_dispatcher"]["project_roles_least_privilege"] is False
    assert broad_result["paused_dispatcher"]["ready"] is False
    assert wrong_result["paused_dispatcher"]["paused_create_invocation_only"] is False
    assert wrong_result["readiness"]["presence_ready"] is False
    assert DISPATCHER_SERVICE_ACCOUNT not in json.dumps(wrong_result)


def test_missing_dispatcher_or_storefront_dispatcher_invocation_blocks_readiness():
    missing = _healthy_responses()
    missing[("gcloud", "run", "jobs", "list")] = (
        f"{readiness.ADS_JOB_ID}\n{readiness.ADS_PAUSED_CREATE_JOB_ID}\n"
    )
    missing_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(missing)[0]
    )

    storefront = _healthy_responses()
    storefront[
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_PAUSED_DISPATCHER_JOB_ID,
        )
    ] = json.dumps(
        {
            "bindings": [
                {
                    "role": "roles/run.invoker",
                    "members": [f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}"],
                }
            ]
        }
    )
    storefront_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(storefront)[0]
    )

    assert missing_result["paused_dispatcher"]["dedicated_job_present"] is False
    assert missing_result["readiness"]["paused_dispatcher_path"] is False
    assert missing_result["readiness"]["presence_ready"] is False
    assert storefront_result["readiness"]["storefront_job_invocation_absent"] is False
    assert storefront_result["readiness"]["presence_ready"] is False


def test_runtime_revision_or_immutable_image_drift_blocks_candidate_readiness():
    revision_drift = _healthy_responses(
        paused_create_job_payload=_job_payload(
            script="scripts/google_ads_paused_worker_job.py",
            env=[
                {
                    "name": "GOOGLE_ADS_DEVELOPER_TOKEN",
                    "valueFrom": {"secretKeyRef": {"name": "google-ads-developer-token"}},
                },
                {
                    "name": "GOOGLE_ADS_CUSTOMER_ID",
                    "valueFrom": {"secretKeyRef": {"name": "google-ads-customer-id"}},
                },
                {"name": "APP_VERSION", "value": "b" * 40},
            ],
        )
    )
    revision_result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(revision_drift)[0]
    )

    image_drift_payload = _dispatcher_job_payload()
    image_drift_payload["spec"]["template"]["spec"]["template"]["spec"]["containers"][0][
        "image"
    ] = f"us-docker.pkg.dev/{PROJECT}/app@sha256:{'e' * 64}"
    image_result = readiness.audit(
        PROJECT,
        "project-go-forward",
        "us-central1",
        runner=_runner(_healthy_responses(dispatcher_job_payload=image_drift_payload))[0],
    )

    assert revision_result["readiness"]["exact_runtime_revision"] is False
    assert revision_result["readiness"]["presence_ready"] is False
    assert image_result["readiness"]["immutable_image_consistent"] is False
    assert image_result["readiness"]["presence_ready"] is False


def test_unbound_optional_ads_secret_access_by_dispatcher_or_storefront_blocks_readiness():
    for member in (
        DISPATCHER_SERVICE_ACCOUNT_MEMBER,
        f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}",
    ):
        responses = _healthy_responses()
        responses[("gcloud", "secrets", "list")] += "google-ads-login-customer-id\n"
        responses[
            (
                "gcloud",
                "secrets",
                "get-iam-policy",
                "google-ads-login-customer-id",
            )
        ] = json.dumps(
            {"bindings": [{"role": "roles/secretmanager.secretAccessor", "members": [member]}]}
        )

        result = readiness.audit(
            PROJECT, "project-go-forward", "us-central1", runner=_runner(responses)[0]
        )

        assert result["readiness"]["presence_ready"] is False
        assert member not in json.dumps(result)


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


def test_storefront_direct_ads_secret_access_blocks_authority_separation():
    responses = _healthy_responses()
    responses[("gcloud", "secrets", "get-iam-policy", "google-ads-developer-token")] = json.dumps(
        {
            "bindings": [
                {
                    "role": "roles/secretmanager.secretAccessor",
                    "members": [
                        SERVICE_ACCOUNT_MEMBER,
                        f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}",
                    ],
                }
            ]
        }
    )
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["storefront_ads_credentials_absent"] is True
    assert result["readiness"]["storefront_secret_access_absent"] is False
    assert result["readiness"]["presence_ready"] is False
    assert STOREFRONT_SERVICE_ACCOUNT not in json.dumps(result)


def test_storefront_project_secret_accessor_blocks_authority_separation():
    responses = _healthy_responses()
    policy = json.loads(responses[("gcloud", "projects", "get-iam-policy")])
    policy["bindings"].append(
        {
            "role": "roles/secretmanager.secretAccessor",
            "members": [f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}"],
        }
    )
    responses[("gcloud", "projects", "get-iam-policy")] = json.dumps(policy)
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["storefront_secret_access_absent"] is False
    assert result["readiness"]["presence_ready"] is False
    assert STOREFRONT_SERVICE_ACCOUNT not in json.dumps(result)


@pytest.mark.parametrize("scope", ["project", "service_account"])
def test_storefront_ads_identity_impersonation_blocks_authority_separation(scope):
    responses = _healthy_responses()
    binding = {
        "role": "roles/iam.serviceAccountTokenCreator",
        "members": [f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}"],
    }
    if scope == "project":
        policy = json.loads(responses[("gcloud", "projects", "get-iam-policy")])
        policy["bindings"].append(binding)
        responses[("gcloud", "projects", "get-iam-policy")] = json.dumps(policy)
    else:
        responses[("gcloud", "iam", "service-accounts", "get-iam-policy")] = json.dumps(
            {"bindings": [binding]}
        )
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["storefront_impersonation_absent"] is False
    assert result["readiness"]["presence_ready"] is False
    assert STOREFRONT_SERVICE_ACCOUNT not in json.dumps(result)


def test_storefront_ads_job_identity_blocks_presence_even_without_secret_env():
    responses = _healthy_responses()
    runtime = json.loads(responses[("gcloud", "run", "services")])
    runtime["spec"]["template"]["spec"]["serviceAccountName"] = SERVICE_ACCOUNT
    responses[("gcloud", "run", "services")] = json.dumps(runtime)
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["runtime"]["ads_credentials_absent"] is True
    assert result["readiness"]["storefront_identity_separated"] is False
    assert result["readiness"]["presence_ready"] is False


def test_storefront_project_job_role_blocks_authority_separation():
    responses = _healthy_responses()
    responses[("gcloud", "projects", "get-iam-policy")] = json.dumps(
        {
            "bindings": [
                {
                    "role": "roles/datastore.user",
                    "members": [SERVICE_ACCOUNT_MEMBER],
                },
                {
                    "role": "roles/run.invoker",
                    "members": [f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}"],
                },
            ]
        }
    )
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["readiness"]["storefront_identity_separated"] is True
    assert result["readiness"]["storefront_job_invocation_absent"] is False
    assert result["readiness"]["presence_ready"] is False
    assert STOREFRONT_SERVICE_ACCOUNT not in json.dumps(result)


def test_storefront_resource_invoker_role_blocks_authority_separation():
    responses = _healthy_responses()
    responses[
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_PAUSED_CREATE_JOB_ID,
        )
    ] = json.dumps(
        {
            "bindings": [
                {
                    "role": "roles/run.invoker",
                    "members": [f"serviceAccount:{STOREFRONT_SERVICE_ACCOUNT}"],
                }
            ]
        }
    )
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["job"]["override_capable_bindings_absent"] is True
    assert result["readiness"]["storefront_job_invocation_absent"] is False
    assert result["readiness"]["presence_ready"] is False


@pytest.mark.parametrize("member", ["allUsers", "allAuthenticatedUsers"])
def test_public_job_invocation_binding_blocks_fixed_protocol(member):
    responses = _healthy_responses()
    responses[
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_PAUSED_CREATE_JOB_ID,
        )
    ] = json.dumps({"bindings": [{"role": "roles/run.invoker", "members": [member]}]})
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["paused_create_job"]["override_capable_bindings_absent"] is False
    assert result["paused_create_job"]["execution_iam_ready"] is False
    assert result["readiness"]["paused_create_path"] is False
    assert result["readiness"]["presence_ready"] is False


def test_paused_create_target_rejects_every_additional_executor():
    responses = _healthy_responses()
    responses[
        (
            "gcloud",
            "run",
            "jobs",
            "get-iam-policy",
            readiness.ADS_PAUSED_CREATE_JOB_ID,
        )
    ] = json.dumps(
        {
            "bindings": [
                {
                    "role": "roles/run.invoker",
                    "members": [
                        DISPATCHER_SERVICE_ACCOUNT_MEMBER,
                        "user:operator@example.invalid",
                    ],
                }
            ]
        }
    )
    result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(responses)[0]
    )

    assert result["paused_create_job"]["execution_iam_ready"] is False
    assert result["readiness"]["paused_create_path"] is False
    assert result["readiness"]["presence_ready"] is False
    assert "operator@example.invalid" not in json.dumps(result)


@pytest.mark.parametrize(
    "role",
    [
        "roles/run.admin",
        "roles/run.developer",
        "roles/run.invoker",
        "roles/run.jobsExecutor",
        "roles/run.jobsExecutorWithOverrides",
    ],
)
def test_paused_create_rejects_project_wide_job_execution_roles(role):
    responses = _healthy_responses()
    policy = json.loads(responses[("gcloud", "projects", "get-iam-policy")])
    policy["bindings"].append({"role": role, "members": ["user:operator@example.invalid"]})
    responses[("gcloud", "projects", "get-iam-policy")] = json.dumps(policy)

    result = readiness.audit(
        PROJECT, "project-go-forward", "us-central1", runner=_runner(responses)[0]
    )

    assert result["paused_create_job"]["project_job_execution_bindings_absent"] is False
    assert result["readiness"]["paused_create_path"] is False
    assert result["readiness"]["presence_ready"] is False
    assert "operator@example.invalid" not in json.dumps(result)


@pytest.mark.parametrize(
    "role",
    [
        "roles/run.admin",
        "roles/run.developer",
        "roles/run.jobsExecutorWithOverrides",
        "projects/tho-ai-agent/roles/custom-job-runner",
    ],
)
def test_override_capable_or_unknown_job_iam_blocks_fixed_protocol(role):
    responses = _healthy_responses()
    responses[("gcloud", "run", "jobs", "get-iam-policy", readiness.ADS_JOB_ID)] = json.dumps(
        {"bindings": [{"role": role, "members": ["user:operator@example.invalid"]}]}
    )
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["job"]["override_capable_bindings_absent"] is False
    assert result["job"]["execution_iam_ready"] is False
    assert result["readiness"]["presence_ready"] is False
    assert "operator@example.invalid" not in json.dumps(result)


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


def test_missing_paused_create_job_blocks_provider_readiness_without_weakening_probe_path():
    responses = _healthy_responses()
    responses[("gcloud", "run", "jobs", "list")] = f"{readiness.ADS_JOB_ID}\n"
    run, _calls = _runner(responses)

    result = readiness.audit(PROJECT, "project-go-forward", "us-central1", runner=run)

    assert result["auth_paths"]["service_account_adc"] is True
    assert result["paused_create_job"]["dedicated_job_present"] is False
    assert result["readiness"]["paused_create_path"] is False
    assert result["readiness"]["presence_ready"] is False
