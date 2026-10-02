"""Prepare-only guards for the DocuSeal deploy and Resend key config path.

These tests lock the placeholder contract. They do not call Resend, DocuSeal,
Secret Manager, or Cloud Run.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_EMPTY_ENV_KEYS = (
    "RESEND_API_KEY=",
    "DOCUSEAL_API_URL=",
    "DOCUSEAL_API_TOKEN=",
    "DOCUSEAL_WEBHOOK_SECRET=",
)


def test_env_example_secret_entries_are_empty_placeholders():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "resend-api-key" in text
    assert "docuseal-api-token" in text
    assert "re_xxxxxxxxxxxxx" not in text
    for key in _EMPTY_ENV_KEYS:
        assert key in text.splitlines()


def test_email_service_reads_resend_key_only_from_env(monkeypatch):
    import email_service

    assert email_service.RESEND_API_KEY_ENV == "RESEND_API_KEY"  # pragma: allowlist secret
    assert email_service.RESEND_API_KEY_SECRET_ID == "resend-api-key"  # pragma: allowlist secret
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert email_service._current_api_key() == ""
    monkeypatch.setenv("RESEND_API_KEY", "placeholder-not-a-real-key")
    assert email_service._current_api_key() == "placeholder-not-a-real-key"

    source = (ROOT / "email_service.py").read_text(encoding="utf-8")
    assert 'os.environ.get(RESEND_API_KEY_ENV, "")' in source
    assert "re_xxxxx" not in source


def test_docuseal_service_names_secret_ids_without_values():
    import docuseal_service

    assert docuseal_service.DOCUSEAL_SECRET_IDS == {
        "DOCUSEAL_API_URL": "docuseal-api-url",
        "DOCUSEAL_API_TOKEN": "docuseal-api-token",
        "DOCUSEAL_WEBHOOK_SECRET": "docuseal-webhook-secret",  # pragma: allowlist secret
    }
    source = (ROOT / "docuseal_service.py").read_text(encoding="utf-8")
    assert 'os.environ.get(DOCUSEAL_API_TOKEN_ENV, "")' in source


def test_template_example_has_null_ids_and_is_not_the_runtime_file():
    example = json.loads(
        (ROOT / "config" / "docuseal_templates.example.json").read_text(encoding="utf-8")
    )
    assert example["_status"] == "NOT_RUN"
    assert example["TMHA_SalesContract.pdf"]["docuseal_template_id"] is None
    assert "docuseal_templates.example.json" not in (ROOT / "docuseal_service.py").read_text(
        encoding="utf-8"
    )


def test_operator_checklist_is_marked_not_run():
    text = (ROOT / "docs" / "OPERATOR_DOCUSEAL_RESEND_CHECKLIST.md").read_text(encoding="utf-8")
    assert "STATUS: NOT RUN" in text
    assert "resend-api-key" in text
    assert "confirm" in text
    assert "services/docuseal/cloudbuild.yaml" in text


def test_app_deploy_does_not_mount_prepare_secrets_yet():
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    active = [
        line
        for line in text.splitlines()
        if "--update-secrets=" in line and not line.strip().startswith("#")
    ]
    assert active == [
        "            --update-secrets=ADMIN_PIN_HASH=admin-pin-hash:latest,ADMIN_SESSION_SECRET=admin-session-secret:latest \\"
    ]


def test_docuseal_workflow_aborts_unless_owner_confirms():
    text = (ROOT / ".github" / "workflows" / "deploy-docuseal.yml").read_text(encoding="utf-8")
    assert 'default: "NO"' in text
    assert "needs: confirm-owner-intent" in text
    assert '!= "YES"' in text
