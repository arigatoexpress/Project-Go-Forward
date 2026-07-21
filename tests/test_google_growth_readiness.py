import json
from types import SimpleNamespace

import scripts.google_growth_readiness as readiness


def _runner(responses):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        key = tuple(command[:3])
        return SimpleNamespace(returncode=0, stdout=responses[key], stderr="")

    return run, calls


def test_audit_reports_only_presence_not_secret_values():
    run, calls = _runner(
        {
            (
                "gcloud",
                "services",
                "list",
            ): "googleads.googleapis.com\nsearchconsole.googleapis.com\n",
            ("gcloud", "secrets", "list"): "google-ads-developer-token\ngoogle-ads-refresh-token\n",
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
    assert result["runtime"]["GA4_MEASUREMENT_ID"] is True
    assert "G-SECRETISH123" not in json.dumps(result)
    assert all("--project=tho-ai-agent" in call for call in calls)


def test_audit_degrades_to_error_without_leaking_stderr():
    def fail(_command, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="token=do-not-print")

    result = readiness.audit("tho-ai-agent", "project-go-forward", "us-central1", runner=fail)
    assert result["errors"] == ["services", "secrets", "runtime"]
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
