"""Tests for tools.lead_nurture — stale-lead re-engagement.

Mocks the Firestore client and the email transport so tests are fully
hermetic. We DO NOT touch the real ``customers`` collection or call Resend.

Coverage:
  * find_stale_leads filters by status, age, and email-set
  * find_stale_leads honors max_results
  * render_nurture_email returns string-typed (subject, html_body) with the
    customer's first name in the subject
  * run_nurture_batch dry_run does NOT call email_service.send_custom_email
  * run_nurture_batch (non-dry-run) calls send_custom_email once per stale lead
"""

from __future__ import annotations

import sys
import types
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import lead_nurture

# ── Fake Firestore (shape-compatible with the production client) ───────────


class _FakeDocSnapshot:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = deepcopy(data)

    def to_dict(self) -> dict:
        return deepcopy(self._data)


class _FakeAddable:
    """Records writes to ``.add(...)`` so tests can assert nurture_log entries."""

    def __init__(self):
        self.added: list[dict] = []

    def add(self, data: dict, timeout: float | None = None) -> None:
        self.added.append(deepcopy(data))


class _FakeQuery:
    def __init__(self, docs: dict[str, dict], filters: list[tuple] | None = None):
        self._docs = docs
        self._filters = filters or []
        self._limit: int | None = None

    def where(self, field: str, op: str, value) -> _FakeQuery:
        q = _FakeQuery(self._docs, [*self._filters, (field, op, value)])
        q._limit = self._limit
        return q

    def limit(self, n: int) -> _FakeQuery:
        q = _FakeQuery(self._docs, list(self._filters))
        q._limit = n
        return q

    def stream(self, timeout: float | None = None):
        emitted = 0
        for doc_id, data in self._docs.items():
            if not self._matches(data):
                continue
            yield _FakeDocSnapshot(doc_id, data)
            emitted += 1
            if self._limit is not None and emitted >= self._limit:
                return

    def _matches(self, data: dict) -> bool:
        for field, op, value in self._filters:
            current = data.get(field)
            if op == "==" and current != value:
                return False
        return True


class _FakeCustomersCollection(_FakeQuery):
    def __init__(self, docs: dict[str, dict]):
        super().__init__(docs)


class _FakeFirestore:
    def __init__(self, customers: dict[str, dict]):
        self._customers = _FakeCustomersCollection(customers)
        self._nurture_log = _FakeAddable()

    def collection(self, name: str):
        if name == "customers":
            return self._customers
        if name == "nurture_log":
            return self._nurture_log
        raise KeyError(name)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def now_utc() -> datetime:
    # Pin "now" inside the customer fixture so tests are deterministic.
    return datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def fake_customers(now_utc) -> dict[str, dict]:
    """A mix of customers covering each filter branch."""
    fresh = now_utc - timedelta(days=2)  # NOT stale (only 2 days old)
    stale_15 = now_utc - timedelta(days=15)
    stale_45 = now_utc - timedelta(days=45)
    stale_30 = now_utc - timedelta(days=30)

    return {
        # Stale lead with email — SHOULD be returned.
        "lead-stale-a": {
            "full_name": "Alice Stale",
            "email": "alice@example.com",
            "status": "LEAD",
            "updated_at": _iso(stale_15),
        },
        # Stale lead, even older — SHOULD be returned, sorted first (oldest).
        "lead-stale-b": {
            "full_name": "Bob Older",
            "email": "bob@example.com",
            "status": "LEAD",
            "updated_at": _iso(stale_45),
        },
        # Lead but only 2 days old — NOT stale.
        "lead-fresh": {
            "full_name": "Carol Fresh",
            "email": "carol@example.com",
            "status": "LEAD",
            "updated_at": _iso(fresh),
        },
        # Stale but no email — must be excluded.
        "lead-no-email": {
            "full_name": "Dan NoEmail",
            "email": "",
            "status": "LEAD",
            "updated_at": _iso(stale_30),
        },
        # Stale but ENROLLED, not LEAD — must be excluded.
        "enrolled-stale": {
            "full_name": "Eve Enrolled",
            "email": "eve@example.com",
            "status": "ENROLLED",
            "updated_at": _iso(stale_30),
        },
        # Stale lead with email but invalid format — must be excluded.
        "lead-bad-email": {
            "full_name": "Frank Bad",
            "email": "not-an-email",
            "status": "LEAD",
            "updated_at": _iso(stale_30),
        },
        # Stale lead using last_contact_at instead of updated_at — included.
        "lead-stale-lastcontact": {
            "full_name": "Grace LastContact",
            "email": "grace@example.com",
            "status": "LEAD",
            "last_contact_at": _iso(stale_30),
            "updated_at": _iso(now_utc),  # newer; should be ignored in favour of last_contact_at
        },
    }


@pytest.fixture(autouse=True)
def fake_firestore(fake_customers, monkeypatch, now_utc):
    """Inject a fake Firestore client into lead_nurture for every test."""
    fake = _FakeFirestore(fake_customers)
    monkeypatch.setattr(lead_nurture, "_firestore_client", fake)
    monkeypatch.setattr(lead_nurture, "_now_utc", lambda: now_utc)
    yield fake
    lead_nurture._reset_client_for_tests()


# ── find_stale_leads ───────────────────────────────────────────────────────


class TestFindStaleLeads:
    def test_filters_by_status_age_and_email(self, fake_firestore):
        results = lead_nurture.find_stale_leads(days_inactive=14)

        ids = {r["customer_id"] for r in results}
        # Should include the stale leads with email (incl. last_contact_at branch).
        assert "lead-stale-a" in ids
        assert "lead-stale-b" in ids
        assert "lead-stale-lastcontact" in ids
        # Should exclude: fresh, no-email, enrolled, invalid-email.
        assert "lead-fresh" not in ids
        assert "lead-no-email" not in ids
        assert "enrolled-stale" not in ids
        assert "lead-bad-email" not in ids

    def test_returns_oldest_first(self, fake_firestore):
        results = lead_nurture.find_stale_leads(days_inactive=14)
        # Bob is 45 days stale; Grace 30; Alice 15. Bob should come first.
        assert results[0]["customer_id"] == "lead-stale-b"

    def test_payload_shape(self, fake_firestore):
        results = lead_nurture.find_stale_leads(days_inactive=14)
        for r in results:
            assert {"customer_id", "name", "email", "last_contact_at", "days_inactive"} <= set(r)
            assert isinstance(r["customer_id"], str)
            assert isinstance(r["email"], str) and "@" in r["email"]
            assert isinstance(r["days_inactive"], int)
            assert r["days_inactive"] >= 14

    def test_max_results_caps_cohort(self, fake_firestore):
        results = lead_nurture.find_stale_leads(days_inactive=14, max_results=1)
        assert len(results) == 1

    def test_threshold_excludes_borderline(self, fake_firestore):
        # 60 days_inactive should only catch the 45-day Bob (via last_contact_at
        # absent → falls back to updated_at). At 100 days, nothing.
        results = lead_nurture.find_stale_leads(days_inactive=100)
        assert results == []

    def test_no_db_returns_empty(self, monkeypatch):
        monkeypatch.setattr(lead_nurture, "_firestore_client", None)
        # Ensure _get_db cannot resolve a real client.
        monkeypatch.setattr(lead_nurture, "_get_db", lambda: None)
        assert lead_nurture.find_stale_leads(days_inactive=14) == []


# ── render_nurture_email ───────────────────────────────────────────────────


class TestRenderNurtureEmail:
    def test_returns_string_tuple(self):
        subject, body = lead_nurture.render_nurture_email(
            {"name": "Alice Stale", "email": "alice@example.com", "days_inactive": 21}
        )
        assert isinstance(subject, str)
        assert isinstance(body, str)
        assert subject and body

    def test_subject_includes_first_name(self):
        subject, _ = lead_nurture.render_nurture_email(
            {"name": "Alice Stale", "email": "alice@example.com", "days_inactive": 21}
        )
        assert "Alice" in subject
        assert "Stale" not in subject  # only the first name

    def test_body_contains_cta_and_utm(self):
        _, body = lead_nurture.render_nurture_email(
            {"name": "Alice Stale", "email": "alice@example.com", "days_inactive": 21}
        )
        assert "utm_source=lead_nurture" in body
        assert "www.texashomeoutlet.com" in body
        assert "sapphirealpha.xyz" not in body

    def test_handles_missing_name_gracefully(self):
        subject, body = lead_nurture.render_nurture_email(
            {"name": "", "email": "x@example.com", "days_inactive": 0}
        )
        assert "Friend" in subject or "Friend" in body


# ── run_nurture_batch ──────────────────────────────────────────────────────


@pytest.fixture
def fake_email_service(monkeypatch):
    """Inject a fake ``email_service`` module so non-dry-run sends are captured."""
    sent: list[dict] = []

    def _fake_send_custom_email(to: str, customer_name: str, subject: str, message: str) -> dict:
        sent.append(
            {
                "to": to,
                "customer_name": customer_name,
                "subject": subject,
                "message": message,
            }
        )
        return {"success": True, "message_id": f"msg_{uuid4().hex[:8]}"}

    fake_module = types.ModuleType("email_service")
    fake_module.send_custom_email = _fake_send_custom_email
    monkeypatch.setitem(sys.modules, "email_service", fake_module)
    yield sent


class TestRunNurtureBatch:
    def test_dry_run_does_not_send(self, fake_firestore, fake_email_service):
        result = lead_nurture.run_nurture_batch(dry_run=True, days_inactive=14)

        assert result["dry_run"] is True
        assert result["cohort_size"] >= 1
        assert result["sent"] == 0
        assert result["failed"] == 0
        # No emails should have been sent.
        assert fake_email_service == []
        # And no nurture_log entries should have been written.
        assert fake_firestore._nurture_log.added == []

    def test_dry_run_returns_previews(self, fake_firestore, fake_email_service):
        result = lead_nurture.run_nurture_batch(dry_run=True, days_inactive=14)
        assert isinstance(result["previews"], list)
        for p in result["previews"]:
            assert {"customer_id", "name", "email", "days_inactive"} <= set(p)

    def test_non_dry_run_sends_once_per_stale_lead(self, fake_firestore, fake_email_service):
        # Find the expected cohort first.
        cohort = lead_nurture.find_stale_leads(days_inactive=14)
        expected_emails = sorted(c["email"] for c in cohort)
        assert expected_emails  # sanity check

        result = lead_nurture.run_nurture_batch(dry_run=False, days_inactive=14)

        assert result["dry_run"] is False
        assert result["cohort_size"] == len(cohort)
        assert result["sent"] == len(cohort)
        assert result["failed"] == 0

        # Exactly one send per stale lead — and to the right addresses.
        assert len(fake_email_service) == len(cohort)
        actual_emails = sorted(s["to"] for s in fake_email_service)
        assert actual_emails == expected_emails

        # Each send should carry a re-engagement subject keyed on first name.
        for send in fake_email_service:
            assert "Still thinking about your new home" in send["subject"]

    def test_non_dry_run_writes_nurture_log_per_send(self, fake_firestore, fake_email_service):
        cohort = lead_nurture.find_stale_leads(days_inactive=14)
        lead_nurture.run_nurture_batch(dry_run=False, days_inactive=14)

        # One nurture_log entry per send.
        log_entries = fake_firestore._nurture_log.added
        assert len(log_entries) == len(cohort)
        for entry in log_entries:
            assert {"customer_id", "email", "subject", "sent_at"} <= set(entry)
            assert entry["send_success"] is True

    def test_no_cohort_no_sends(self, fake_firestore, fake_email_service):
        # Threshold so high nothing is stale.
        result = lead_nurture.run_nurture_batch(dry_run=False, days_inactive=10_000)
        assert result["cohort_size"] == 0
        assert result["sent"] == 0
        assert result["failed"] == 0
        assert fake_email_service == []
