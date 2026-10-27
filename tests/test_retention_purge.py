"""Retention purge safety: fakes only, never a live Firestore project."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from retention_purge import (
    PRODUCTION_PROJECT_ID,
    PURGE_ALLOWLIST,
    PURGE_COLLECTIONS,
    RetentionConfigError,
    load_retention_policy,
    main,
    parse_timestamp,
    run_purge,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parent.parent

# Proposed periods shipped in config.yaml. PROPOSED, owner to confirm.
EXPECTED_PERIODS = {
    "chat_sessions": (90, "updated_at"),
    "conversation_contexts": (90, "updated_at"),
    "leads": (730, "created_at"),
    "appointments": (730, "created_at"),
    "feedback": (730, "created_at"),
    "tho_admin_login_codes": (7, "created_at"),
    "google_ads_owner_step_up_nonces": (7, "issued_at"),
    "user_activity_log": (90, "timestamp"),
    "analytics_events": (180, "created_at"),
    "email_log": (365, "sent_at"),
    "email_reply_drafts": (90, "created_at"),
    "nurture_log": (365, "sent_at"),
}


class FakeDB:
    def __init__(self, docs, *, fail_stream=()):
        self.docs = {name: dict(items) for name, items in docs.items()}
        self.fail_stream = set(fail_stream)
        self.collection_calls = []
        self.deleted = []
        self.batch_sizes = []

    def collection(self, name):
        self.collection_calls.append(name)
        return FakeCollection(self, name)

    def batch(self):
        return FakeBatch(self)


class FakeCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def stream(self, **kwargs):
        if self.name in self.db.fail_stream:
            raise RuntimeError("stream failed")
        for doc_id, data in list(self.db.docs.get(self.name, {}).items()):
            yield FakeSnap(doc_id, data)

    def document(self, doc_id):
        return FakeRef(self.db, self.name, doc_id)


class FakeSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class FakeRef:
    def __init__(self, db, name, doc_id):
        self.db = db
        self.name = name
        self.id = doc_id

    def delete(self, **kwargs):
        self.db.docs.get(self.name, {}).pop(self.id, None)
        self.db.deleted.append((self.name, self.id))


class FakeBatch:
    def __init__(self, db):
        self.db = db
        self.ops = []

    def delete(self, ref):
        self.ops.append(ref)

    def commit(self, **kwargs):
        self.db.batch_sizes.append(len(self.ops))
        for ref in self.ops:
            ref.delete()


class ExplodingDB:
    def collection(self, name):
        raise AssertionError(f"should not read {name}")

    def batch(self):
        raise AssertionError("should not delete")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _policy(collections, *, enabled=True, batch_size=100):
    return load_retention_policy(
        {
            "data_retention": {
                "enabled": enabled,
                "batch_size": batch_size,
                "collections": collections,
            }
        }
    )


def _lead_spec(days=30):
    return {"retention_days": days, "timestamp_field": "created_at"}


def test_shipped_config_is_proposed_and_disabled():
    text = (REPO / "config.yaml").read_text(encoding="utf-8")
    assert "PROPOSED, owner to confirm" in text
    config = yaml.safe_load(text)
    section = config["data_retention"]
    assert section["enabled"] is False
    assert section["batch_size"] == 100
    assert set(section["collections"]) == set(EXPECTED_PERIODS)
    for name, (days, timestamp_field) in EXPECTED_PERIODS.items():
        spec = section["collections"][name]
        assert spec["retention_days"] == days
        assert spec["timestamp_field"] == timestamp_field
    assert dict(PURGE_COLLECTIONS) == {name: field for name, (_, field) in EXPECTED_PERIODS.items()}
    policy = load_retention_policy(config)
    assert policy.enabled is False
    assert policy.collections == ()


def test_live_business_and_credential_collections_are_not_eligible():
    blocked = {
        "customers",
        "deals",
        "inventory",
        "sales",
        "leases",
        "tax_payments",
        "service_requests",
        "tho_admin_credentials",
        "tho_admin_staff",
        "audit_log",
        "activities",
        "google_ads_deployments",
    }
    assert blocked.isdisjoint(PURGE_ALLOWLIST)


def test_disabled_config_is_a_noop_even_with_execute():
    policy = load_retention_policy(
        {
            "data_retention": {
                "enabled": False,
                "batch_size": "not-a-number",
                "collections": {
                    "customers": {"retention_days": 1, "timestamp_field": "created_at"}
                },
            }
        }
    )
    report = run_purge(ExplodingDB(), policy, execute=True, now=NOW)
    assert report.mode == "disabled"
    assert report.deleted == 0
    assert report.collections == []


def test_dry_run_deletes_nothing_and_reports_a_few_ids():
    docs = {}
    for index in range(7):
        docs[f"d{index}"] = {
            "created_at": _iso(NOW - timedelta(days=40 + index)),
            "phone": "555-0100",
            "email": "visitor@example.test",
        }
    db = FakeDB({"leads": docs})
    policy = _policy({"leads": _lead_spec()})
    report = run_purge(db, policy, execute=False, now=NOW)
    assert report.mode == "dry_run"
    assert report.deleted == 0
    assert db.deleted == []
    assert db.batch_sizes == []
    assert set(db.docs["leads"]) == {f"d{index}" for index in range(7)}
    lead_report = next(item for item in report.collections if item.collection == "leads")
    assert lead_report.eligible == 7
    assert lead_report.would_delete == 7
    assert lead_report.sample_ids == ["d6", "d5", "d4", "d3", "d2"]
    blob = json.dumps(report.to_dict())
    assert "555-0100" not in blob
    assert "visitor@example.test" not in blob


def test_batch_cap_deletes_only_the_oldest_expired_docs():
    db = FakeDB(
        {
            "leads": {
                "oldest": {"created_at": _iso(NOW - timedelta(days=100))},
                "older": {"created_at": _iso(NOW - timedelta(days=80))},
                "old": {"created_at": _iso(NOW - timedelta(days=40))},
                "fresh": {"created_at": _iso(NOW - timedelta(days=1))},
            }
        }
    )
    policy = _policy({"leads": _lead_spec(30)}, batch_size=2)
    report = run_purge(db, policy, execute=True, now=NOW)
    assert db.deleted == [("leads", "oldest"), ("leads", "older")]
    assert db.batch_sizes == [2]
    assert set(db.docs["leads"]) == {"old", "fresh"}
    lead_report = next(item for item in report.collections if item.collection == "leads")
    assert lead_report.eligible == 3
    assert lead_report.would_delete == 2
    assert lead_report.deleted == 2
    assert lead_report.capped is True
    assert lead_report.sample_ids == ["oldest", "older"]
    assert report.deleted == 2


def test_unexpired_docs_and_exact_cutoff_are_kept():
    cutoff = NOW - timedelta(days=30)
    db = FakeDB(
        {
            "leads": {
                "epoch-old": {"created_at": (cutoff - timedelta(days=2)).timestamp()},
                "boundary": {"created_at": cutoff},
                "naive-new": {
                    "created_at": (cutoff + timedelta(seconds=1)).replace(tzinfo=None).isoformat()
                },
                "z-old": {
                    "created_at": (cutoff - timedelta(days=1)).isoformat().replace("+00:00", "Z")
                },
                "aware-old": {"created_at": cutoff - timedelta(hours=1)},
            }
        }
    )
    policy = _policy({"leads": _lead_spec(30)})
    report = run_purge(db, policy, execute=True, now=NOW)
    assert set(db.docs["leads"]) == {"boundary", "naive-new"}
    deleted_ids = {doc_id for _, doc_id in db.deleted}
    assert deleted_ids == {"epoch-old", "z-old", "aware-old"}
    lead_report = next(item for item in report.collections if item.collection == "leads")
    assert lead_report.skipped_malformed == 0
    assert lead_report.eligible == 3


def test_malformed_timestamps_are_skipped():
    old = _iso(NOW - timedelta(days=90))
    fresh = _iso(NOW - timedelta(days=1))
    db = FakeDB(
        {
            "leads": {
                "good-old": {"created_at": old},
                "good-new": {"created_at": fresh},
                "missing": {"name": "no stamp"},
                "empty": {"created_at": "  "},
                "garbage": {"created_at": "not-a-date"},
                "numeric-string": {"created_at": "1710000000"},
                "bool": {"created_at": True},
                "list": {"created_at": [2020, 1, 1]},
                "zero": {"created_at": 0},
                "nan": {"created_at": float("nan")},
                "none": {"created_at": None},
            }
        }
    )
    policy = _policy({"leads": _lead_spec(30)})
    report = run_purge(db, policy, execute=True, now=NOW)
    assert db.deleted == [("leads", "good-old")]
    assert "good-new" in db.docs["leads"]
    for doc_id in (
        "missing",
        "empty",
        "garbage",
        "numeric-string",
        "bool",
        "list",
        "zero",
        "nan",
        "none",
    ):
        assert doc_id in db.docs["leads"]
    lead_report = next(item for item in report.collections if item.collection == "leads")
    assert lead_report.skipped_malformed == 9
    assert lead_report.eligible == 1


def test_ineligible_collection_is_not_scanned_or_deleted():
    db = FakeDB(
        {
            "customers": {"c1": {"created_at": _iso(NOW - timedelta(days=800)), "full_name": "A"}},
            "leads": {"old": {"created_at": _iso(NOW - timedelta(days=40))}},
        }
    )
    policy = _policy(
        {
            "leads": _lead_spec(30),
            "customers": {"retention_days": 1, "timestamp_field": "created_at"},
        }
    )
    report = run_purge(db, policy, execute=True, now=NOW)
    assert "customers" not in db.collection_calls
    assert "c1" in db.docs["customers"]
    assert db.deleted == [("leads", "old")]
    rejected = [item for item in report.collections if item.status == "rejected"]
    assert [item.collection for item in rejected] == ["customers"]


def test_timestamp_field_mismatch_is_rejected():
    db = FakeDB(
        {"leads": {"old": {"created_at": _iso(NOW - timedelta(days=40)), "email": "a@b.c"}}}
    )
    policy = _policy({"leads": {"retention_days": 30, "timestamp_field": "email"}})
    report = run_purge(db, policy, execute=True, now=NOW)
    assert db.deleted == []
    assert "old" in db.docs["leads"]
    assert "leads" not in db.collection_calls
    lead_report = next(item for item in report.collections if item.collection == "leads")
    assert lead_report.status == "rejected"


def test_scan_failure_deletes_nothing():
    db = FakeDB(
        {
            "chat_sessions": {"s-old": {"updated_at": _iso(NOW - timedelta(days=200))}},
            "leads": {"l-old": {"created_at": _iso(NOW - timedelta(days=200))}},
        },
        fail_stream={"leads"},
    )
    policy = _policy(
        {
            "chat_sessions": {"retention_days": 30, "timestamp_field": "updated_at"},
            "leads": _lead_spec(30),
        }
    )
    with pytest.raises(RuntimeError, match="stream failed"):
        run_purge(db, policy, execute=True, now=NOW)
    assert db.deleted == []
    assert "s-old" in db.docs["chat_sessions"]
    assert "l-old" in db.docs["leads"]


def test_enabled_batch_size_must_be_a_sane_integer():
    with pytest.raises(RetentionConfigError):
        _policy({"leads": _lead_spec()}, batch_size=0)
    with pytest.raises(RetentionConfigError):
        _policy({"leads": _lead_spec()}, batch_size=501)
    with pytest.raises(RetentionConfigError):
        load_retention_policy(
            {"data_retention": {"enabled": True, "batch_size": True, "collections": {}}}
        )


def test_parse_timestamp_rejects_unusable_values():
    assert parse_timestamp(None) is None
    assert parse_timestamp(True) is None
    assert parse_timestamp("not-a-date") is None
    assert parse_timestamp("1710000000") is None
    assert parse_timestamp(0) is None
    assert parse_timestamp(float("inf")) is None
    parsed = parse_timestamp("2026-01-02T03:04:05Z")
    assert parsed == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_main_shipped_config_does_not_connect(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PRODUCTION_PROJECT_ID)
    monkeypatch.delenv("THO_RETENTION_PURGE_ALLOW_PRODUCTION", raising=False)

    def factory(_project):
        raise AssertionError("should not connect")

    assert main(["--execute"], client_factory=factory) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "disabled"
    assert payload["deleted"] == 0
    assert payload["collections"] == []


def test_main_refuses_production_project(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PRODUCTION_PROJECT_ID)
    monkeypatch.delenv("THO_RETENTION_PURGE_ALLOW_PRODUCTION", raising=False)
    calls = []
    config = {
        "data_retention": {
            "enabled": True,
            "batch_size": 10,
            "collections": {"leads": _lead_spec()},
        }
    }
    code = main([], config=config, client_factory=lambda project: calls.append(project))
    assert code == 2
    assert calls == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "refused"
    assert payload["deleted"] == 0


def test_main_refuses_missing_project_when_enabled(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    calls = []
    config = {
        "data_retention": {
            "enabled": True,
            "collections": {"leads": _lead_spec()},
        }
    }
    code = main([], config=config, client_factory=lambda project: calls.append(project))
    assert code == 2
    assert calls == []
    assert json.loads(capsys.readouterr().out)["mode"] == "refused"


def test_main_execute_on_nonproduction_deletes_capped_batch(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tho-retention-test")
    wall = datetime.now(UTC)
    db = FakeDB(
        {
            "leads": {
                "old": {"created_at": _iso(wall - timedelta(days=40))},
                "new": {"created_at": _iso(wall - timedelta(days=1))},
            }
        }
    )
    config = {
        "data_retention": {
            "enabled": True,
            "batch_size": 10,
            "collections": {"leads": _lead_spec(30)},
        }
    }

    def factory(project):
        assert project == "tho-retention-test"
        return db

    assert main(["--execute"], config=config, client_factory=factory) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "execute"
    assert payload["deleted"] == 1
    assert db.deleted == [("leads", "old")]
    assert "new" in db.docs["leads"]
