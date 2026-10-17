"""Mop-up coverage for google_ads_admin tails, field_map_loader, esign/email-reply tails.

Targets (measured with the main-venv Python 3.11):
  * google_ads_admin.approval_routes — readiness 503 tail, contract-changed tails,
    remediation line, LedgerWriteError / passthrough / generic 503 tails
  * google_ads_admin.routes — generic-exception 503 tails on GET readiness + POST draft
  * google_ads_admin.status — invalid-contract raise, _iso str passthrough,
    event allowlist raise, record mismatch raise, pre-approval outbox raise
  * google_ads_admin.dispatcher — config ValueErrors + _default_transport
  * config.field_map_loader — every accessor incl. missing-template defaults
  * esign_review — _amount parse tail, _finite_money_inputs tail, $0 payment /
    $0 insurance-escrow branches, non-widget / parentless-widget annots, XFA tails
  * email_reply_drafts — lazy client success path, degradation tails

Note: status.py line 34 is unreachable dead code — validate_draft() pins
deployment.key to "tho-search-high-intent-huffman-v1", which always satisfies
_DEPLOYMENT_KEY, so the second raise can never execute.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config.field_map_loader as field_map_loader  # noqa: E402
import email_reply_drafts as drafts  # noqa: E402
import esign_review  # noqa: E402
from auth.google_ads_step_up import (  # noqa: E402
    StepUpCaps,
    StepUpContext,
    StepUpNonce,
    build_evidence_envelope,
    context_digest,
    email_hash,
    hash_value,
    issue_proof_reference,
)
from auth.session import SessionManager  # noqa: E402
from google_ads_admin import approval_routes, routes  # noqa: E402
from google_ads_admin.dispatcher import FixedCloudRunJobDispatcher  # noqa: E402
from google_ads_admin.status import (  # noqa: E402
    _event_projection,
    _iso,
    build_deployment_readiness,
    load_checked_in_contract,
)
from scripts.google_ads_access_evidence import (  # noqa: E402
    AccessCheckKey,
    AccessEvidenceStatus,
    build_access_evidence,
)
from scripts.google_ads_launch_draft import contract_sha256  # noqa: E402
from scripts.google_ads_paused_worker import (  # noqa: E402
    DraftReviewControlPlane,
    InMemoryAuthorityLedger,
    InvalidStateTransition,
    LedgerWriteError,
    StaticContractSource,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
CSRF = "cov-c13-b-csrf"
OWNER = "aristotlespec@gmail.com"


def _contract():
    return load_checked_in_contract()


def _identity():
    contract = _contract()
    digest = contract_sha256(contract)
    return contract, f"{contract['deployment']['key']}--{digest}", f"sha256:{digest}"


def _access_evidence():
    _contract, deployment_id, _hash = _identity()
    return build_access_evidence(
        deployment_id=deployment_id,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision="a" * 40,
        now=NOW,
    )


# ─── config/field_map_loader.py ──────────────────────────────────────────────


class TestFieldMapLoader:
    def test_accessors_return_config_for_real_template(self):
        name = "TMHA_SalesContract.pdf"
        config = field_map_loader.get_template_config(name)
        assert config is not None
        assert field_map_loader.get_template_field_map(name) == config["field_map"]
        assert field_map_loader.get_template_checkbox_fields(name) == config.get(
            "checkbox_fields", {}
        )
        assert field_map_loader.get_template_static_values(name) == config.get(
            "static_values", {}
        )
        assert field_map_loader.get_template_required_fields(name) == config.get(
            "required_fields", []
        )
        assert field_map_loader.get_template_pii_fields(name) == config.get("pii_fields", [])
        definitions = field_map_loader.get_fields_for_template(name)
        assert definitions
        for key in definitions:
            assert field_map_loader.get_field_definition(key) == definitions[key]

    def test_missing_template_returns_defaults(self):
        assert field_map_loader.get_template_config("no-such-template.pdf") is None
        assert field_map_loader.get_template_field_map("no-such-template.pdf") is None
        assert field_map_loader.get_template_checkbox_fields("no-such-template.pdf") == {}
        assert field_map_loader.get_template_static_values("no-such-template.pdf") == {}
        assert field_map_loader.get_template_required_fields("no-such-template.pdf") == []
        assert field_map_loader.get_template_pii_fields("no-such-template.pdf") == []
        assert field_map_loader.get_fields_for_template("no-such-template.pdf") == {}
        assert field_map_loader.get_packet_config("no-such-packet") is None

    def test_master_schema_packets_and_categories(self):
        field_map = field_map_loader.get_field_map()
        assert field_map_loader.get_field_definitions() == field_map["data_field_definitions"]
        assert field_map_loader.get_field_definition("sales_price") is not None
        assert field_map_loader.get_field_definition("no_such_field") is None
        assert field_map_loader.get_packet_config("standard_closing") is not None
        assert field_map_loader.get_form_set_rules() == field_map.get("form_set_rules", {})

        listed = field_map_loader.list_templates()
        assert len(listed) == len(field_map["templates"])
        sample = next(item for item in listed if item["template_name"] == "TMHA_SalesContract.pdf")
        assert sample["field_count"] == len(field_map["templates"][sample["template_name"]]["field_map"])
        contracts = field_map_loader.get_templates_by_category(sample["category"])
        assert sample in contracts
        assert all(item["category"] == sample["category"] for item in contracts)

        packets = field_map_loader.list_packets()
        assert packets
        by_name = {p["packet_name"]: p for p in packets}
        assert by_name["standard_closing"]["templates"] == field_map["packets"][
            "standard_closing"
        ]["templates"]

    def test_reload_refreshes_cache(self):
        before = field_map_loader.get_field_map()
        field_map_loader.reload()
        assert field_map_loader.get_field_map() == before


# ─── google_ads_admin/status.py ──────────────────────────────────────────────


class TestStatusTails:
    def test_invalid_contract_file_raises_value_error(self, tmp_path):
        bad = tmp_path / "contract.json"
        bad.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="reviewed Google Ads contract is invalid"):
            load_checked_in_contract(bad)

    def test_deployment_key_guard_is_structurally_unreachable(self):
        """status.py line 34 can never fire: validate_draft() pins deployment.key
        to the one value that always satisfies _DEPLOYMENT_KEY, so a contract that
        passes validation can never fail the key regex. Pins the invariant."""
        import google_ads_admin.status as status_mod
        from scripts.google_ads_launch_draft import validate_draft

        contract = _contract()
        assert validate_draft(contract) == []
        assert status_mod._DEPLOYMENT_KEY.fullmatch(contract["deployment"]["key"])

    def test_iso_passes_through_non_datetime(self):
        assert _iso("2026-08-13T12:00:00Z") == "2026-08-13T12:00:00Z"
        assert _iso(NOW) == "2026-08-13T12:00:00Z"

    def test_event_projection_rejects_record_from_another_deployment(self):
        contract = _contract()
        control = DraftReviewControlPlane(
            InMemoryAuthorityLedger(clock=lambda: NOW), StaticContractSource(contract)
        )
        record = control.ensure_internal_draft()
        # Self-consistent event (its own contract_hash matches its own
        # deployment_id per the record validator) that belongs to a DIFFERENT
        # deployment than the record being projected.
        foreign_digest = "b" * 64
        event = {
            "event_id": "00000000000000000001-internal-draft-created",
            "deployment_id": f"{record.deployment_key}--{foreign_digest}",
            "contract_hash": f"sha256:{foreign_digest}",
            "event_type": "INTERNAL_DRAFT_CREATED",
            "record_version": 1,
            "from_state": None,
            "to_state": "INTERNAL_DRAFT",
            "error_code": None,
            "occurred_at": NOW,
        }
        with pytest.raises(ValueError, match="authority event is outside"):
            _event_projection(event, record)

    def test_projection_rejects_record_from_another_contract(self):
        contract = _contract()
        ledger = InMemoryAuthorityLedger(clock=lambda: NOW)
        record = DraftReviewControlPlane(ledger, StaticContractSource(contract)).ensure_internal_draft()
        foreign = replace(record, deployment_id=f"{record.deployment_key}--{'c' * 64}")
        # The mismatch is detected before any event projection happens.
        with pytest.raises(ValueError, match="durable Google Ads record"):
            build_deployment_readiness(foreign, [])

    def _events(self, record, versions):
        specs = {
            1: ("INTERNAL_DRAFT_CREATED", None, "INTERNAL_DRAFT"),
            2: ("SERVER_VALIDATED", "INTERNAL_DRAFT", "SERVER_VALIDATED"),
        }
        return [
            {
                "event_id": f"{version:020d}-{specs[version][0].lower().replace('_', '-')}",
                "deployment_id": record.deployment_id,
                "contract_hash": record.contract_hash,
                "event_type": specs[version][0],
                "record_version": version,
                "from_state": specs[version][1],
                "to_state": specs[version][2],
                "error_code": None,
                "occurred_at": NOW,
            }
            for version in versions
        ]

    def test_projection_rejects_outbox_state_before_approval(self):
        contract = _contract()
        ledger = InMemoryAuthorityLedger(clock=lambda: NOW)
        record = DraftReviewControlPlane(ledger, StaticContractSource(contract)).ensure_internal_draft()
        with pytest.raises(ValueError, match="pre-approval deployment cannot have an outbox"):
            build_deployment_readiness(record, self._events(record, [1]), outbox_state="PENDING")
        validated = DraftReviewControlPlane(
            ledger, StaticContractSource(contract)
        ).server_validate(record.deployment_id, expected_version=1)
        with pytest.raises(ValueError, match="pre-approval deployment cannot have an outbox"):
            build_deployment_readiness(
                validated,
                self._events(validated, [1, 2]),
                outbox_state="DISPATCHED",
            )


# ─── google_ads_admin/routes.py ──────────────────────────────────────────────


def _admin_client(monkeypatch, ledger):
    import main

    monkeypatch.setattr(main, "_verify_admin_token", lambda _token: True)
    monkeypatch.setattr(routes, "get_authority_ledger", lambda: ledger)
    return TestClient(main.app, raise_server_exceptions=False)


class TestReviewRouteTails:
    def test_readiness_surfaces_503_when_contract_cannot_load(self, monkeypatch):
        ledger = InMemoryAuthorityLedger(clock=lambda: NOW)
        client = _admin_client(monkeypatch, ledger)
        monkeypatch.setattr(
            routes,
            "load_checked_in_contract",
            lambda: (_ for _ in ()).throw(RuntimeError("raw contract detail")),
        )

        response = client.get(
            "/api/admin/google-ads/deployment-readiness", headers={"X-Admin-Token": "t"}
        )

        assert response.status_code == 503
        assert "raw contract detail" not in response.text

    def test_bootstrap_surfaces_503_when_ledger_write_fails(self, monkeypatch):
        ledger = InMemoryAuthorityLedger(clock=lambda: NOW)
        client = _admin_client(monkeypatch, ledger)
        ledger.fail_next_write()

        response = client.post("/api/admin/google-ads/draft", headers={"X-Admin-Token": "t"}, json={})

        assert response.status_code == 503
        assert response.json()["message"] == "Paid Search deployment draft is unavailable."
        assert ledger._records == {}


# ─── google_ads_admin/approval_routes.py ─────────────────────────────────────


def _approval_env(monkeypatch):
    values = {
        "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION": "a" * 40,
        "APP_VERSION": "a" * 40,
        "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT": "tho-ai-agent",
        "THO_GOOGLE_ADS_PAUSED_CREATE_REGION": "us-central1",
        "THO_GOOGLE_ADS_PAUSED_CREATE_JOB": "google-growth-paused-create",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _proof_manager():
    return SessionManager(secret_key="cov-c13-b-approval-secret")


def _envelope(*, contract_hash=None):
    _contract, deployment_id, real_hash = _identity()
    evidence = _access_evidence()
    caps = StepUpCaps(
        average_daily_usd=20,
        max_single_day_charge_usd=40,
        monthly_charge_limit_usd=608,
        max_cpc_usd=5,
    )
    context = StepUpContext(
        deployment_id=deployment_id,
        contract_hash=contract_hash or real_hash,
        caps=caps,
        evidence_digest=evidence.evidence_digest,
    )
    nonce = StepUpNonce(
        nonce_hash=hash_value("cov-c13-b-nonce"),
        context_digest=context_digest(context),
        owner_email_hash=email_hash(OWNER),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    return build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value(b"cov-c13-b-key"),
        verified_at=NOW,
    )


def _approval_body(manager, **overrides):
    envelope = _envelope()
    body = {
        "deployment_id": envelope.deployment_id,
        "expected_version": 2,
        "proof_reference": issue_proof_reference(manager, envelope),
        "proof_id": envelope.evidence_id,
        "access_evidence_id": envelope.evidence_digest,
    }
    body.update(overrides)
    return body


class _ApprovalLedger:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def approve_paused_create_with_proof(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {
            "deployment_id": kwargs["deployment_id"],
            "contract_hash": kwargs["contract_hash"],
            "state": "PAUSED_CREATE_APPROVED",
            "version": 3,
            "outbox_state": "PENDING",
            "replayed": False,
        }


@pytest.fixture
def approval_client(monkeypatch):
    _approval_env(monkeypatch)
    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", OWNER)
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", OWNER)
    manager = _proof_manager()
    ledger = _ApprovalLedger()
    app = FastAPI()
    app.dependency_overrides[approval_routes.get_session_manager] = lambda: manager
    app.dependency_overrides[approval_routes.get_approval_ledger] = lambda: ledger
    app.dependency_overrides[approval_routes.require_owner_step_up] = lambda: OWNER
    app.include_router(approval_routes.router)
    client = TestClient(app, base_url="https://www.texashomeoutlet.com")
    return client, manager, ledger


class TestApprovalRouteTails:
    def test_checked_in_identity_rejects_changed_deployment(self):
        _contract, real_deployment_id, _hash = _identity()
        other = f"{real_deployment_id.rsplit('--', 1)[0]}--{'d' * 64}"
        assert other != real_deployment_id
        request = approval_routes.PausedCreateApprovalRequest.model_validate(
            {
                "deployment_id": other,
                "expected_version": 2,
                "proof_reference": "p" * 40,
                "proof_id": f"sha256:{'e' * 64}",
                "access_evidence_id": f"sha256:{'f' * 64}",
            }
        )
        with pytest.raises(InvalidStateTransition, match="reviewed contract changed"):
            approval_routes._checked_in_identity(request)

    def test_readiness_remediation_names_missing_server_validation(self, monkeypatch):
        _approval_env(monkeypatch)

        class Ledger:
            def get(self, _deployment_id):
                return SimpleNamespace(
                    state=SimpleNamespace(value="INTERNAL_DRAFT"), version=1
                )

            def get_access_evidence(self, *_args):
                raise RuntimeError("no evidence yet")

        readiness = approval_routes._read_approval_readiness(Ledger(), clock=lambda: NOW)
        assert readiness.action_available is False
        assert "Complete offline server validation." in readiness.remediation

    def test_readiness_route_fails_closed_with_503_on_ledger_error(self, monkeypatch):
        class BrokenLedger:
            def get(self, _deployment_id):
                raise RuntimeError("raw ledger detail")

        monkeypatch.setenv(
            "THO_GOOGLE_ADS_OWNER_EMAILS",
            OWNER,
        )
        app = FastAPI()
        app.dependency_overrides[approval_routes.get_approval_ledger] = (
            lambda: BrokenLedger()
        )
        app.dependency_overrides[approval_routes.require_owner_step_up] = lambda: OWNER
        app.include_router(approval_routes.router)
        client = TestClient(app, base_url="https://www.texashomeoutlet.com")

        response = client.get("/api/admin/google-ads/paused-create-approval-readiness")

        assert response.status_code == 503
        assert "raw ledger detail" not in response.text

    def test_proof_contract_hash_drift_guard_is_structurally_unreachable(self):
        """The line-259 `proof.contract_hash != contract_hash` drift guard can never
        fire: ``_verify_request_proof`` binds ``proof.deployment_id`` to
        ``request.deployment_id``, ``_checked_in_identity`` binds
        ``request.deployment_id`` to the current contract digest, and
        ``StepUpContext`` binds ``contract_hash`` to the ``deployment_id`` suffix —
        all from a single digest. This test pins that invariant so the guard's
        unreachability is deliberate, not accidental."""
        from auth.google_ads_step_up import StepUpContext

        source = Path(approval_routes.__file__).read_text(encoding="utf-8")
        assert "proof.contract_hash != contract_hash" in source
        with pytest.raises(Exception, match="deployment identity does not match"):
            StepUpContext(
                deployment_id=f"tho-search-high-intent-huffman-v1--{'a' * 64}",
                contract_hash=f"sha256:{'b' * 64}",
                caps=StepUpCaps(
                    average_daily_usd=20,
                    max_single_day_charge_usd=40,
                    monthly_charge_limit_usd=608,
                    max_cpc_usd=5,
                ),
                evidence_digest=f"sha256:{'c' * 64}",
            )

    def test_approval_503_on_ledger_write_error(self, approval_client):
        client, manager, ledger = approval_client
        ledger.error = LedgerWriteError("ledger_write_failed")

        response = client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=_approval_body(manager),
        )

        assert response.status_code == 503
        assert len(ledger.calls) == 1

    def test_approval_passes_through_http_exception_unchanged(self, approval_client):
        client, manager, ledger = approval_client
        ledger.error = HTTPException(status_code=503, detail="ledger custom 503")

        response = client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=_approval_body(manager),
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "ledger custom 503"

    def test_approval_503_on_unexpected_ledger_failure(self, approval_client):
        client, manager, ledger = approval_client
        ledger.error = RuntimeError("raw unexpected detail")

        response = client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=_approval_body(manager),
        )

        assert response.status_code == 503
        assert "raw unexpected detail" not in response.text


# ─── google_ads_admin/dispatcher.py ──────────────────────────────────────────


class TestDispatcherTails:
    def test_invalid_fixed_target_rejected(self):
        with pytest.raises(ValueError, match="fixed dispatcher configuration is invalid"):
            FixedCloudRunJobDispatcher(
                project="THO-INVALID", region="us-central1", job="google-growth-paused-create"
            )

    def test_timeout_bounds_enforced(self):
        with pytest.raises(ValueError, match="dispatcher timeout must be between 1 and 30"):
            FixedCloudRunJobDispatcher(
                project="tho-ai-agent",
                region="us-central1",
                job="google-growth-paused-create",
                timeout_seconds=0,
            )
        with pytest.raises(ValueError, match="dispatcher timeout must be between 1 and 30"):
            FixedCloudRunJobDispatcher(
                project="tho-ai-agent",
                region="us-central1",
                job="google-growth-paused-create",
                timeout_seconds=31,
            )

    def test_default_transport_builds_authorized_session(self, monkeypatch):
        posts = []

        class _Response:
            ok = True

        class _Session:
            def post(self, url, *, json, timeout):
                posts.append((url, json, timeout))
                return _Response()

        import types

        fake_google_auth = types.ModuleType("google.auth")
        fake_google_auth.__path__ = []  # behave as a package for submodule imports
        fake_google_auth.default = lambda scopes: (
            SimpleNamespace(scopes=scopes),
            "tho-ai-agent",
        )
        fake_transport = types.ModuleType("google.auth.transport")
        fake_transport.__path__ = []
        fake_requests = types.ModuleType("google.auth.transport.requests")
        fake_requests.AuthorizedSession = lambda credentials: _Session()
        monkeypatch.setitem(sys.modules, "google.auth", fake_google_auth)
        monkeypatch.setitem(sys.modules, "google.auth.transport", fake_transport)
        monkeypatch.setitem(sys.modules, "google.auth.transport.requests", fake_requests)
        # `import google.auth` binds the real `google` package and reads the
        # attribute, so the parent package attribute must be patched too.
        import google

        monkeypatch.setattr(google, "auth", fake_google_auth, raising=False)

        dispatcher = FixedCloudRunJobDispatcher(
            project="tho-ai-agent",
            region="us-central1",
            job="google-growth-paused-create",
        )
        dispatcher.invoke()

        assert posts == [
            (
                "https://run.googleapis.com/v2/projects/tho-ai-agent/locations/us-central1/"
                "jobs/google-growth-paused-create:run",
                {},
                10,
            )
        ]


# ─── esign_review.py ─────────────────────────────────────────────────────────


def _doc(**overrides):
    data = {
        "buyer_first_name": "Alice",
        "buyer_last_name": "Buyer",
        "buyer_email": "alice@example.com",
        "sales_price": 80000,
        "down_payment": 5000,
        "loan_term": "240",
        "apr": "8.5",
        "tax_rate": 2.0,
        "annual_insurance": 1200,
    }
    data.update(overrides)
    return data


class TestEsignTails:
    def test_amount_returns_none_for_unparseable_text(self):
        assert esign_review._amount("not-a-number") is None
        assert esign_review._amount(True) is None
        assert esign_review._amount("") is None

    def test_non_finite_money_inputs_keep_parse_failures_but_drop_infinity(self):
        problems = esign_review.money_problems(_doc(sales_price="NaN"))
        assert [p["field"] for p in problems] == ["sales_price"]
        # Non-numeric text is unparseable (not non-finite): it is left in place
        # and simply reads as blank downstream.
        problems = esign_review.money_problems(_doc(sales_price="abc"))
        assert [p["field"] for p in problems] == ["sales_price"]

    def test_zero_stored_monthly_payment_is_flagged(self):
        problems = esign_review.money_problems(_doc(monthly_payment=0))
        assert [p["field"] for p in problems] == ["monthly_payment"]
        assert "works out to $0" in problems[0]["message"]

    def test_rounded_away_insurance_escrow_is_flagged(self):
        problems = esign_review.money_problems(_doc(annual_insurance=0.001))
        assert [p["field"] for p in problems] == ["escrow"]
        assert "insurance escrow works out to $0" in problems[0]["message"]

    def test_read_filled_values_skips_non_widget_and_parentless_annot(self, tmp_path):
        from pypdf import PdfWriter
        from pypdf.generic import (
            ArrayObject,
            DictionaryObject,
            NameObject,
            NumberObject,
        )

        rect = ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)])
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        page = writer.pages[0]
        non_widget = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Text"),
                NameObject("/Rect"): rect,
            }
        )
        orphan_widget = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): rect,
            }
        )
        page[NameObject("/Annots")] = ArrayObject([non_widget, orphan_widget])
        path = tmp_path / "annot-tails.pdf"
        with open(path, "wb") as handle:
            writer.write(handle)

        assert esign_review.read_filled_values(str(path)) == {}

    def test_xfa_reader_without_xfa_returns_empty(self):
        reader = SimpleNamespace(trailer={"/Root": {"/AcroForm": {}}})
        assert esign_review._read_xfa_values(reader) == {}

    def test_xfa_reader_with_malformed_dataset_swallows_error(self):
        class _BrokenStream:
            def get_object(self):
                return self

            def get_data(self):
                return b"<unclosed"

        reader = SimpleNamespace(
            trailer={"/Root": {"/AcroForm": {"/XFA": ["datasets", _BrokenStream()]}}}
        )
        assert esign_review._read_xfa_values(reader) == {}

    def test_xfa_reader_without_datasets_section_returns_empty(self):
        class _Stream:
            def get_object(self):
                return self

            def get_data(self):
                return b"<x/>"

        reader = SimpleNamespace(
            trailer={"/Root": {"/AcroForm": {"/XFA": ["config", _Stream()]}}}
        )
        assert esign_review._read_xfa_values(reader) == {}


# ─── email_reply_drafts.py ───────────────────────────────────────────────────


class _ExplodingRef:
    def get(self, timeout=None):
        raise RuntimeError("firestore exploded")

    def update(self, data, timeout=None):  # pragma: no cover - never reached
        raise AssertionError("unreachable")


class _ExplodingDB:
    def __init__(self, *, fail_on="document"):
        self._fail_on = fail_on

    def collection(self, _name):
        if self._fail_on == "document":
            return SimpleNamespace(document=lambda _id: _ExplodingRef())
        return SimpleNamespace(order_by=self._order_by)

    def _order_by(self, *_args, **_kwargs):
        raise RuntimeError("firestore exploded")


class TestEmailReplyDraftTails:
    def test_lazy_client_success_path_uses_injected_firestore_module(self, monkeypatch):
        sentinel = object()
        fake_module = SimpleNamespace(Client=lambda **kwargs: sentinel)
        monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake_module)
        import google.cloud

        monkeypatch.setattr(google.cloud, "firestore", fake_module, raising=False)
        drafts._reset_client_for_tests()
        try:
            assert drafts._get_db() is sentinel
            # Second call reuses the cached client.
            assert drafts._get_db() is sentinel
        finally:
            drafts._reset_client_for_tests()

    def test_create_degrades_to_none_when_store_read_raises(self, monkeypatch):
        monkeypatch.setattr(drafts, "_firestore_client", _ExplodingDB())
        result, created = drafts.create_draft(
            message_id="m-explode", sender="a@b.com", subject="s", triage_label="substantive"
        )
        assert result is None
        assert created is False

    def test_get_draft_rejects_blank_id_and_store_failures(self, monkeypatch):
        db = _ExplodingDB()
        monkeypatch.setattr(drafts, "_firestore_client", db)
        assert drafts.get_draft("") is None
        assert drafts.get_draft("some-draft-id") is None

    def test_list_drafts_degrades_to_empty_on_query_failure(self, monkeypatch):
        monkeypatch.setattr(drafts, "_firestore_client", _ExplodingDB(fail_on="order_by"))
        assert drafts.list_drafts() == []

    def test_set_draft_body_returns_none_without_store(self, monkeypatch):
        monkeypatch.setattr(drafts, "_get_db", lambda: None)
        assert drafts.set_draft_body("any-id", "body") is None

    def test_set_draft_body_returns_none_for_missing_draft(self, monkeypatch):
        from tests.test_email_reply_drafts import FakeDB

        monkeypatch.setattr(drafts, "_firestore_client", FakeDB())
        assert drafts.set_draft_body("no-such-draft", "body") is None
