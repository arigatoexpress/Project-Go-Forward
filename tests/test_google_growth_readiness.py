import json
from types import SimpleNamespace

import scripts.google_growth_readiness as readiness


def _runner(responses):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        matches = [
            (key, output)
            for key, output in responses.items()
            if tuple(command[: len(key)]) == key
        ]
        key, output = max(matches, key=lambda item: len(item[0]))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    return run, calls


def _job_payload(
    *,
    service_account="google-growth-control@tho-ai-agent.iam.gserviceaccount.com",
    args=None,
    env=None,
):
    if args is None:
        args = ["scripts/google_ads_access_probe.py", "--live"]
    if env is None:
        env = [
            {
                "name": "GOOGLE_ADS_DEVELOPER_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {"name": "google-ads-developer-token"}
                },
            },
            {
                "name": "GOOGLE_ADS_CUSTOMER_ID",
                "valueFrom": {"secretKeyRef": {"name": "google-ads-customer-id"}},
            },
        ]
    return {
        "spec": {
            "template": {
                "spec": {
                    "template": {
                        "spec": {
                            "serviceAccountName": service_account,
                            "containers": [{"args": args, "env": env}],
                        }
                    }
                }
            }
        }
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
            ("gcloud", "iam", "service-accounts"): "691674245427-compute@developer.gserviceaccount.com\n",
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
    run, _calls = _runner(
        {
            ("gcloud", "services", "list"): "\n".join(readiness.SERVICES),
            ("gcloud", "secrets", "list"): (
                "google-ads-developer-token\ngoogle-ads-customer-id\n"
            ),
            ("gcloud", "iam", "service-accounts"): (
                "google-growth-control@tho-ai-agent.iam.gserviceaccount.com\n"
            ),
            ("gcloud", "iam", "service-accounts", "keys"): "",
            ("gcloud", "run", "jobs", "list"): "google-growth-control\n",
            ("gcloud", "run", "jobs", "describe"): json.dumps(_job_payload()),
            ("gcloud", "run", "services"): json.dumps(
                [{"name": "GTM_CONTAINER_ID", "value": "GTM-DO-NOT-LEAK"}]
            ),
        }
    )

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"] == {
        "dedicated_identity_present": True,
        "persistent_user_key_absent": True,
    }
    assert result["job"] == {
        "dedicated_job_present": True,
        "dedicated_identity_attached": True,
        "managed_secret_bindings": True,
        "live_probe_configured": True,
        "persistent_key_path_absent": True,
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
        "measurement": True,
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
            ("gcloud", "secrets", "list"): (
                "google-ads-developer-token\ngoogle-ads-customer-id\n"
            ),
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
    expected = "google-growth-control@tho-ai-agent.iam.gserviceaccount.com"

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
                    "valueFrom": {
                        "secretKeyRef": {"name": "google-ads-customer-id"}
                    },
                },
            ]
        ),
        expected,
    )

    assert wrong_identity["runtime_ready"] is False
    assert missing_live["runtime_ready"] is False
    assert plaintext_token["managed_secret_bindings"] is False
    assert plaintext_token["runtime_ready"] is False


def test_user_managed_service_account_key_blocks_keyless_auth_path():
    run, _calls = _runner(
        {
            ("gcloud", "services", "list"): "\n".join(readiness.SERVICES),
            ("gcloud", "secrets", "list"): (
                "google-ads-developer-token\ngoogle-ads-customer-id\n"
            ),
            ("gcloud", "iam", "service-accounts"): (
                "google-growth-control@tho-ai-agent.iam.gserviceaccount.com\n"
            ),
            ("gcloud", "iam", "service-accounts", "keys"): (
                "projects/tho-ai-agent/serviceAccounts/redacted/keys/redacted\n"
            ),
            ("gcloud", "run", "jobs", "list"): "google-growth-control\n",
            ("gcloud", "run", "jobs", "describe"): json.dumps(_job_payload()),
            ("gcloud", "run", "services"): json.dumps(
                [{"name": "GA4_MEASUREMENT_ID", "value": "redacted"}]
            ),
        }
    )

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"]["persistent_user_key_absent"] is False
    assert result["job"]["runtime_ready"] is True
    assert result["auth_paths"]["service_account_adc"] is False
    assert "keys/redacted" not in json.dumps(result)


def test_service_account_without_a_runnable_dedicated_job_is_not_an_auth_path():
    run, _calls = _runner(
        {
            ("gcloud", "services", "list"): "\n".join(readiness.SERVICES),
            ("gcloud", "secrets", "list"): (
                "google-ads-developer-token\ngoogle-ads-customer-id\n"
            ),
            ("gcloud", "iam", "service-accounts"): (
                "google-growth-control@tho-ai-agent.iam.gserviceaccount.com\n"
            ),
            ("gcloud", "iam", "service-accounts", "keys"): "",
            ("gcloud", "run", "jobs", "list"): "",
            ("gcloud", "run", "services"): json.dumps(
                [{"name": "GA4_MEASUREMENT_ID", "value": "redacted"}]
            ),
        }
    )

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=run)

    assert result["service_account"]["dedicated_identity_present"] is True
    assert result["job"]["dedicated_job_present"] is False
    assert result["auth_paths"]["service_account_adc"] is False
    assert result["readiness"]["presence_ready"] is False
