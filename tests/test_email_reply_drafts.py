"""Tests for email_reply_drafts.py — the human-review draft store.

THE STATE MACHINE IS THE SPEC:

    pending  → approved | rejected | expired
    approved → sent
    (everything else is illegal and must raise)

Covers:
  * create_draft persists a document with the expected schema
  * idempotency: same Resend message-id never creates a second draft
    (webhook retries must never double-draft, and later never double-send)
  * get_draft / list_drafts round-trip
  * legal status transitions succeed and stamp decided_by/updated_at
  * illegal transitions (sent→anything, rejected→approved, pending→sent,
    approved→approved, unknown status) raise IllegalTransitionError
  * store degrades safely (returns None, never raises) when Firestore is
    unavailable
  * no PII beyond the operational minimum: raw body stored only as a capped
    excerpt

Run: python -m pytest tests/test_email_reply_drafts.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import email_reply_drafts as drafts  # noqa: E402
from email_reply_drafts import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SENT,
    IllegalTransitionError,
    ReplyDraft,
    create_draft,
    get_draft,
    list_drafts,
    transition,
)

# ─── Firestore stand-in ────────────────────────────────────────────────────


class _Snap:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self.id = doc_id

    def get(self, timeout=None):
        return _Snap(self.id, self._store.get(self.id))

    def set(self, data: dict, timeout=None):
        self._store[self.id] = dict(data)

    def update(self, data: dict, timeout=None):
        if self.id not in self._store:
            raise KeyError(self.id)
        self._store[self.id].update(dict(data))


class FakeQuery:
    def __init__(self, store: dict, filters=None, limit_value=None):
        self._store = store
        self._filters = list(filters or [])
        self._limit = limit_value

    def where(self, field, op, value):
        assert op == "=="
        return FakeQuery(self._store, self._filters + [(field, value)], self._limit)

    def limit(self, n):
        return FakeQuery(self._store, self._filters, n)

    def stream(self, timeout=None):
        emitted = 0
        for doc_id, data in list(self._store.items()):
            if all(data.get(f) == v for f, v in self._filters):
                if self._limit is not None and emitted >= self._limit:
                    return
                emitted += 1
                yield _Snap(doc_id, data)


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def document(self, doc_id: str):
        return FakeDocRef(self.docs, doc_id)

    def where(self, field, op, value):
        return FakeQuery(self.docs).where(field, op, value)

    def limit(self, n):
        return FakeQuery(self.docs).limit(n)

    def stream(self, timeout=None):
        return FakeQuery(self.docs).stream()


class FakeDB:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def collection(self, name: str):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(drafts, "_firestore_client", db)
    yield db
    drafts._reset_client_for_tests()


def _make(fake_db, message_id="msg-001", **overrides) -> ReplyDraft:
    kwargs = dict(
        message_id=message_id,
        sender="customer@example.com",
        subject="Interested in a home",
        triage_label="substantive",
        rule_hits=["trigger:money"],
        inbound_excerpt="How much does the Bluebonnet model cost?",
        lead_id="email_abc123",
        draft_body="",
    )
    kwargs.update(overrides)
    draft, created = create_draft(**kwargs)
    assert draft is not None
    return draft


# ─── Creation & schema ─────────────────────────────────────────────────────


class TestCreateDraft:
    def test_creates_pending_draft_with_expected_schema(self, fake_db):
        draft = _make(fake_db)
        assert draft.status == STATUS_PENDING
        assert draft.message_id == "msg-001"
        assert draft.sender == "customer@example.com"
        assert draft.subject == "Interested in a home"
        assert draft.triage_label == "substantive"
        assert draft.rule_hits == ["trigger:money"]
        assert draft.lead_id == "email_abc123"
        assert draft.draft_body == ""
        assert draft.created_at and draft.updated_at
        assert draft.decided_by == ""
        # Persisted exactly one Firestore doc, keyed deterministically.
        col = fake_db.collections[drafts.DRAFTS_COLLECTION]
        assert len(col.docs) == 1
        assert draft.draft_id in col.docs

    def test_created_flag_true_then_false_on_duplicate(self, fake_db):
        d1, created1 = create_draft(
            message_id="msg-dup", sender="a@b.com", subject="s", triage_label="substantive"
        )
        d2, created2 = create_draft(
            message_id="msg-dup", sender="a@b.com", subject="s", triage_label="substantive"
        )
        assert created1 is True
        assert created2 is False
        assert d1.draft_id == d2.draft_id

    def test_idempotent_on_message_id_even_with_different_payload(self, fake_db):
        d1 = _make(fake_db, message_id="msg-42", subject="first")
        d2, created = create_draft(
            message_id="msg-42", sender="x@y.com", subject="second", triage_label="safe_ack"
        )
        # Webhook retry with the same message id NEVER overwrites the original.
        assert created is False
        assert d2.draft_id == d1.draft_id
        assert d2.subject == "first"
        col = fake_db.collections[drafts.DRAFTS_COLLECTION]
        assert len(col.docs) == 1

    def test_distinct_message_ids_create_distinct_drafts(self, fake_db):
        d1 = _make(fake_db, message_id="msg-a")
        d2 = _make(fake_db, message_id="msg-b")
        assert d1.draft_id != d2.draft_id

    def test_missing_message_id_rejected(self, fake_db):
        result, created = create_draft(
            message_id="", sender="a@b.com", subject="s", triage_label="substantive"
        )
        assert result is None
        assert created is False

    def test_inbound_excerpt_is_capped(self, fake_db):
        draft = _make(fake_db, inbound_excerpt="x" * 10_000)
        assert len(draft.inbound_excerpt) <= drafts.MAX_EXCERPT_CHARS

    def test_no_db_degrades_to_none(self, monkeypatch):
        monkeypatch.setattr(drafts, "_get_db", lambda: None)
        result, created = create_draft(
            message_id="msg-nodb", sender="a@b.com", subject="s", triage_label="substantive"
        )
        assert result is None
        assert created is False


# ─── Read paths ────────────────────────────────────────────────────────────


class TestReadPaths:
    def test_get_draft_roundtrip(self, fake_db):
        created = _make(fake_db)
        fetched = get_draft(created.draft_id)
        assert fetched is not None
        assert fetched.draft_id == created.draft_id
        assert fetched.status == STATUS_PENDING

    def test_get_draft_missing_returns_none(self, fake_db):
        assert get_draft("no-such-draft") is None

    def test_list_drafts_filters_by_status(self, fake_db):
        _make(fake_db, message_id="m1")
        d2 = _make(fake_db, message_id="m2")
        transition(d2.draft_id, STATUS_REJECTED, actor="tester")
        pending = list_drafts(status=STATUS_PENDING)
        rejected = list_drafts(status=STATUS_REJECTED)
        assert [d.message_id for d in pending] == ["m1"]
        assert [d.message_id for d in rejected] == ["m2"]

    def test_list_drafts_all(self, fake_db):
        _make(fake_db, message_id="m1")
        _make(fake_db, message_id="m2")
        assert len(list_drafts()) == 2

    def test_list_drafts_no_db_returns_empty(self, monkeypatch):
        monkeypatch.setattr(drafts, "_get_db", lambda: None)
        assert list_drafts() == []


# ─── State machine ─────────────────────────────────────────────────────────


class TestTransitions:
    @pytest.mark.parametrize(
        "target", [STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED]
    )
    def test_pending_legal_transitions(self, fake_db, target):
        draft = _make(fake_db)
        updated = transition(draft.draft_id, target, actor="ari")
        assert updated is not None
        assert updated.status == target
        assert updated.decided_by == "ari"
        assert updated.updated_at >= draft.updated_at

    def test_approved_to_sent(self, fake_db):
        draft = _make(fake_db)
        transition(draft.draft_id, STATUS_APPROVED, actor="ari")
        updated = transition(draft.draft_id, STATUS_SENT, actor="system:sender")
        assert updated.status == STATUS_SENT

    @pytest.mark.parametrize(
        ("path", "bad_target"),
        [
            ([], STATUS_SENT),  # pending → sent: NEVER skip approval
            ([], STATUS_PENDING),  # pending → pending
            ([STATUS_REJECTED], STATUS_APPROVED),  # rejected is terminal
            ([STATUS_REJECTED], STATUS_SENT),
            ([STATUS_EXPIRED], STATUS_SENT),  # expired is terminal
            ([STATUS_APPROVED], STATUS_APPROVED),
            ([STATUS_APPROVED], STATUS_REJECTED),  # approval is one-way
            ([STATUS_APPROVED, STATUS_SENT], STATUS_APPROVED),  # sent is terminal
            ([STATUS_APPROVED, STATUS_SENT], STATUS_REJECTED),
        ],
    )
    def test_illegal_transitions_raise(self, fake_db, path, bad_target):
        draft = _make(fake_db)
        for step in path:
            transition(draft.draft_id, step, actor="ari")
        with pytest.raises(IllegalTransitionError):
            transition(draft.draft_id, bad_target, actor="ari")

    def test_unknown_target_status_raises(self, fake_db):
        draft = _make(fake_db)
        with pytest.raises(IllegalTransitionError):
            transition(draft.draft_id, "shipped", actor="ari")

    def test_transition_missing_draft_returns_none(self, fake_db):
        assert transition("no-such-draft", STATUS_APPROVED, actor="ari") is None

    def test_transition_no_db_returns_none(self, monkeypatch):
        monkeypatch.setattr(drafts, "_get_db", lambda: None)
        assert transition("whatever", STATUS_APPROVED, actor="ari") is None

    def test_set_draft_body_only_while_pending(self, fake_db):
        draft = _make(fake_db)
        updated = drafts.set_draft_body(draft.draft_id, "Hello, thanks for reaching out.")
        assert updated.draft_body == "Hello, thanks for reaching out."
        transition(draft.draft_id, STATUS_APPROVED, actor="ari")
        with pytest.raises(IllegalTransitionError):
            drafts.set_draft_body(draft.draft_id, "sneaky post-approval edit")


# ─── Safety invariants ─────────────────────────────────────────────────────


class TestSafetyInvariants:
    def test_module_never_imports_email_service(self):
        """The draft store must have no send capability whatsoever."""
        import email_reply_drafts as module

        source = Path(module.__file__).read_text()
        import_lines = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        assert not any("email_service" in line for line in import_lines)
        assert not any("resend" in line.lower() for line in import_lines)
        assert "send_email" not in source

    def test_doc_id_is_deterministic_hash_of_message_id(self, fake_db):
        d1 = _make(fake_db, message_id="stable-id")
        assert d1.draft_id == drafts._doc_id_for_message("stable-id")
        # Not the raw message id (avoid Firestore doc-id charset issues).
        assert d1.draft_id != "stable-id"
