"""Tests for chat_history.py — chat message/session persistence with Firestore.

Covers:
  * ChatMessage dataclass round-trip
  * ChatSession dataclass round-trip, add_message, get_summary
  * ChatHistory Firestore operations: get_or_create_session, save_session,
    add_message, get_session, get_user_sessions, get_recent_sessions,
    update_session_status, get_context_for_agent, search_conversations
  * Edge cases: empty sessions, missing documents, None values, large payloads

Run: python -m pytest tests/test_chat_history.py -v
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─── Fixed time for determinism ────────────────────────────────────────────


class FixedDatetime(datetime):
    @classmethod
    def utcnow(cls):
        return cls(2026, 6, 13, 12, 0, 0)


# ─── Firestore stand-in ────────────────────────────────────────────────────


class _Snap:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data else None


class FakeDocumentRef:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self.id = doc_id

    def get(self):
        data = self._store.get(self.id)
        return _Snap(self.id, data)

    def set(self, data: dict):
        self._store[self.id] = dict(data)

    def update(self, data: dict):
        if self.id not in self._store:
            self._store[self.id] = {}
        self._store[self.id].update(dict(data))


class FakeQuery:
    def __init__(self, store: dict, filters=None, limit=None, order_by=None):
        self._store = store
        self._filters = list(filters or [])
        self._limit = limit
        self._order_by = order_by

    def where(self, field: str, op: str, value):
        return FakeQuery(
            self._store, self._filters + [(field, op, value)], self._limit, self._order_by
        )

    def order_by(self, field, direction=None):
        return FakeQuery(self._store, self._filters, self._limit, (field, direction))

    def limit(self, n: int):
        return FakeQuery(self._store, self._filters, n, self._order_by)

    def stream(self):
        results = []
        for doc_id, data in self._store.items():
            ok = True
            for field, op, value in self._filters:
                actual = data.get(field)
                if op == "==" and actual != value:
                    ok = False
                    break
                if op == ">=" and not (actual is not None and actual >= value):
                    ok = False
                    break
            if ok:
                results.append((doc_id, dict(data)))

        if self._order_by:
            field, direction = self._order_by
            reverse = direction == "DESCENDING"
            results.sort(key=lambda x: x[1].get(field, ""), reverse=reverse)

        count = 0
        for doc_id, data in results:
            yield _Snap(doc_id, data)
            count += 1
            if self._limit is not None and count >= self._limit:
                break


class FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str):
        return FakeDocumentRef(self._store, doc_id)

    def where(self, field: str, op: str, value):
        return FakeQuery(self._store).where(field, op, value)

    def limit(self, n: int):
        return FakeQuery(self._store, limit=n)

    def stream(self):
        for doc_id, data in self._store.items():
            yield _Snap(doc_id, dict(data))


class FakeFirestore:
    def __init__(self, **kwargs):
        self._collections: dict[str, dict] = {}
        self.collections = self._collections

    def collection(self, name: str):
        return FakeCollection(self._collections.setdefault(name, {}))


FakeFirestoreModule = types.SimpleNamespace(
    Client=FakeFirestore,
    Query=types.SimpleNamespace(DESCENDING="DESCENDING"),
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    """Freeze utcnow() so every timestamp is deterministic."""
    import chat_history
    monkeypatch.setattr(chat_history, "datetime", FixedDatetime)
    import conversation_memory
    monkeypatch.setattr(conversation_memory, "datetime", FixedDatetime)


@pytest.fixture
def fake_chat_db(monkeypatch):
    """Swap chat_history's Firestore client for an in-memory fake."""
    import chat_history
    fake = FakeFirestore()

    class FakeClient:
        def __init__(self, **kwargs):
            self._fake = fake
            self.collections = fake.collections

        def collection(self, name):
            return fake.collection(name)

    fake_module = types.SimpleNamespace(
        Client=FakeClient,
        Query=types.SimpleNamespace(DESCENDING="DESCENDING"),
    )
    monkeypatch.setattr(chat_history, "firestore", fake_module)
    yield fake


# ─── ChatMessage tests ─────────────────────────────────────────────────────


def test_chat_message_round_trip():
    """ChatMessage serializes and deserializes faithfully."""
    from chat_history import ChatMessage

    msg = ChatMessage(role="user", text="hello", timestamp="2026-06-13T10:00:00")
    d = msg.to_dict()
    restored = ChatMessage.from_dict(d)
    assert restored.role == "user"
    assert restored.text == "hello"
    assert restored.timestamp == "2026-06-13T10:00:00"
    assert restored.message_id == msg.message_id


def test_chat_message_auto_generates_ids():
    """When message_id is None, __post_init__ fills it in."""
    from chat_history import ChatMessage

    msg = ChatMessage(role="model", text="hi", timestamp="2026-06-13T10:00:00")
    assert msg.message_id is not None
    assert msg.message_id.startswith("msg_")


# ─── ChatSession tests ─────────────────────────────────────────────────────


def test_chat_session_round_trip():
    """ChatSession serializes and deserializes faithfully."""
    from chat_history import ChatMessage, ChatSession

    session = ChatSession(
        session_id="s1",
        user_id="u1",
        messages=[ChatMessage(role="user", text="hello", timestamp="2026-06-13T10:00:00")],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={"source": "web"},
        status="active",
        lead_id="lead-1",
    )
    d = session.to_dict()
    restored = ChatSession.from_dict(d)
    assert restored.session_id == "s1"
    assert restored.user_id == "u1"
    assert len(restored.messages) == 1
    assert restored.messages[0].role == "user"
    assert restored.metadata == {"source": "web"}
    assert restored.status == "active"
    assert restored.lead_id == "lead-1"


def test_chat_session_add_message():
    """add_message appends a message and bumps updated_at."""
    from chat_history import ChatSession

    session = ChatSession(
        session_id="s1",
        user_id="u1",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    msg = session.add_message("user", "hello")
    assert len(session.messages) == 1
    assert session.messages[0].text == "hello"
    assert session.updated_at != "2026-06-13T09:00:00"
    assert msg.role == "user"


def test_chat_session_get_summary():
    """get_summary counts user and AI messages."""
    from chat_history import ChatSession

    session = ChatSession(
        session_id="s1",
        user_id="u1",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    session.add_message("user", "a")
    session.add_message("model", "b")
    session.add_message("user", "c")
    assert session.get_summary() == "2 user messages, 1 AI responses"


def test_chat_session_empty_messages():
    """Empty messages list is handled gracefully."""
    from chat_history import ChatSession

    session = ChatSession(
        session_id="s1",
        user_id="u1",
        messages=None,
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata=None,
        status=None,
    )
    assert session.messages == []
    assert session.metadata == {}
    assert session.status == "active"
    assert session.get_summary() == "0 user messages, 0 AI responses"


# ─── ChatHistory tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_session_existing(fake_chat_db):
    """Existing document is returned unmodified."""
    from chat_history import ChatHistory, ChatSession

    existing = ChatSession(
        session_id="s1",
        user_id="u1",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={"foo": "bar"},
        status="active",
    )
    fake_chat_db.collection("chat_sessions").document("s1").set(existing.to_dict())

    history = ChatHistory(project_id="test")
    session = await history.get_or_create_session("s1", "u1")
    assert session.session_id == "s1"
    assert session.metadata == {"foo": "bar"}


@pytest.mark.asyncio
async def test_get_or_create_session_new(fake_chat_db):
    """Missing document creates a new session and persists it."""
    from chat_history import ChatHistory

    history = ChatHistory(project_id="test")
    session = await history.get_or_create_session("s2", "u2", metadata={"source": "test"})
    assert session.session_id == "s2"
    assert session.user_id == "u2"
    assert session.metadata == {"source": "test"}
    assert session.status == "active"

    stored = fake_chat_db.collection("chat_sessions").document("s2").get()
    assert stored.exists
    assert stored.to_dict()["user_id"] == "u2"


@pytest.mark.asyncio
async def test_save_session(fake_chat_db):
    """save_session writes the full document."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    session = ChatSession(
        session_id="s3",
        user_id="u3",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    await history.save_session(session)

    stored = fake_chat_db.collection("chat_sessions").document("s3").get()
    assert stored.exists
    assert stored.to_dict()["session_id"] == "s3"


@pytest.mark.asyncio
async def test_add_message(fake_chat_db):
    """add_message creates session if missing, appends message, saves."""
    from chat_history import ChatHistory

    history = ChatHistory(project_id="test")
    msg = await history.add_message("s4", "u4", "user", "hello world")
    assert msg.role == "user"
    assert msg.text == "hello world"

    stored = fake_chat_db.collection("chat_sessions").document("s4").get()
    assert stored.exists
    assert len(stored.to_dict()["messages"]) == 1
    assert stored.to_dict()["messages"][0]["text"] == "hello world"


@pytest.mark.asyncio
async def test_get_session_found(fake_chat_db):
    """get_session returns ChatSession when document exists."""
    from chat_history import ChatHistory, ChatSession

    session = ChatSession(
        session_id="s5",
        user_id="u5",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    fake_chat_db.collection("chat_sessions").document("s5").set(session.to_dict())

    history = ChatHistory(project_id="test")
    result = await history.get_session("s5")
    assert result is not None
    assert result.session_id == "s5"


@pytest.mark.asyncio
async def test_get_session_missing(fake_chat_db):
    """get_session returns None for missing documents."""
    from chat_history import ChatHistory

    history = ChatHistory(project_id="test")
    result = await history.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_user_sessions(fake_chat_db):
    """get_user_sessions filters by user_id, status, and limit."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")

    for i in range(3):
        s = ChatSession(
            session_id=f"s{i}",
            user_id="u6",
            messages=[],
            created_at=f"2026-06-13T0{i}:00:00",
            updated_at=f"2026-06-13T0{i}:00:00",
            metadata={},
            status="active" if i < 2 else "closed",
        )
        fake_chat_db.collection("chat_sessions").document(f"s{i}").set(s.to_dict())

    all_sessions = await history.get_user_sessions("u6", limit=10)
    assert len(all_sessions) == 3

    active = await history.get_user_sessions("u6", limit=10, status="active")
    assert len(active) == 2
    assert all(s.status == "active" for s in active)

    limited = await history.get_user_sessions("u6", limit=2)
    assert len(limited) == 2

    # Ordering (DESCENDING by updated_at)
    assert limited[0].updated_at >= limited[1].updated_at


@pytest.mark.asyncio
async def test_get_recent_sessions(fake_chat_db):
    """get_recent_sessions returns sessions updated within the window."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")

    old = ChatSession(
        session_id="old",
        user_id="u7",
        messages=[],
        created_at="2026-06-01T00:00:00",
        updated_at="2026-06-01T00:00:00",
        metadata={},
        status="active",
    )
    fake_chat_db.collection("chat_sessions").document("old").set(old.to_dict())

    recent = ChatSession(
        session_id="recent",
        user_id="u7",
        messages=[],
        created_at="2026-06-13T11:00:00",
        updated_at="2026-06-13T11:00:00",
        metadata={},
        status="active",
    )
    fake_chat_db.collection("chat_sessions").document("recent").set(recent.to_dict())

    results = await history.get_recent_sessions(hours=24, limit=100)
    assert len(results) == 1
    assert results[0].session_id == "recent"


@pytest.mark.asyncio
async def test_update_session_status(fake_chat_db):
    """update_session_status writes status and optional lead_id."""
    from chat_history import ChatHistory

    history = ChatHistory(project_id="test")
    fake_chat_db.collection("chat_sessions").document("s8").set(
        {"session_id": "s8", "user_id": "u8", "status": "active"}
    )

    await history.update_session_status("s8", "converted", lead_id="lead-8")
    stored = fake_chat_db.collection("chat_sessions").document("s8").get()
    assert stored.to_dict()["status"] == "converted"
    assert stored.to_dict()["lead_id"] == "lead-8"

    await history.update_session_status("s8", "closed")
    stored = fake_chat_db.collection("chat_sessions").document("s8").get()
    assert stored.to_dict()["status"] == "closed"
    assert "lead_id" in stored.to_dict()


@pytest.mark.asyncio
async def test_get_context_for_agent(fake_chat_db):
    """get_context_for_agent formats messages for the Gemini API."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    session = ChatSession(
        session_id="s9",
        user_id="u9",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    session.add_message("user", "hello")
    session.add_message("model", "hi there")
    session.add_message("user", "what homes?")
    fake_chat_db.collection("chat_sessions").document("s9").set(session.to_dict())

    context = await history.get_context_for_agent("s9", "u9", max_messages=2)
    assert len(context) == 2
    assert context[0] == {"role": "model", "parts": [{"text": "hi there"}]}
    assert context[1] == {"role": "user", "parts": [{"text": "what homes?"}]}


@pytest.mark.asyncio
async def test_get_context_for_agent_empty_session(fake_chat_db):
    """Empty session yields empty context list."""
    from chat_history import ChatHistory

    history = ChatHistory(project_id="test")
    context = await history.get_context_for_agent("empty", "u0", max_messages=10)
    assert context == []


@pytest.mark.asyncio
async def test_search_conversations_matching(fake_chat_db):
    """search_conversations finds sessions containing the query text."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    s1 = ChatSession(
        session_id="sA",
        user_id="uA",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    s1.add_message("user", "I need a double wide home")
    s2 = ChatSession(
        session_id="sB",
        user_id="uB",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    s2.add_message("user", "Looking for modular")
    fake_chat_db.collection("chat_sessions").document("sA").set(s1.to_dict())
    fake_chat_db.collection("chat_sessions").document("sB").set(s2.to_dict())

    results = await history.search_conversations("double wide")
    assert len(results) == 1
    assert results[0]["session_id"] == "sA"
    assert "double wide" in results[0]["message_preview"].lower()


@pytest.mark.asyncio
async def test_search_conversations_non_matching(fake_chat_db):
    """search_conversations returns empty list when no matches."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    s = ChatSession(
        session_id="sC",
        user_id="uC",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    s.add_message("user", "hello")
    fake_chat_db.collection("chat_sessions").document("sC").set(s.to_dict())

    results = await history.search_conversations("zzzzzzzz")
    assert results == []


@pytest.mark.asyncio
async def test_search_conversations_preview_truncation(fake_chat_db):
    """Long messages are truncated with '...' in the preview."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    s = ChatSession(
        session_id="sD",
        user_id="uD",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    long_text = "a" * 200
    s.add_message("user", long_text)
    fake_chat_db.collection("chat_sessions").document("sD").set(s.to_dict())

    results = await history.search_conversations("a")
    assert len(results) == 1
    assert results[0]["message_preview"].endswith("...")
    assert len(results[0]["message_preview"]) == 103  # 100 + "..."


@pytest.mark.asyncio
async def test_search_conversations_limit(fake_chat_db):
    """search_conversations respects the limit parameter."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    for i in range(5):
        s = ChatSession(
            session_id=f"s{i}",
            user_id=f"u{i}",
            messages=[],
            created_at="2026-06-13T09:00:00",
            updated_at="2026-06-13T09:00:00",
            metadata={},
            status="active",
        )
        s.add_message("user", f"query match {i}")
        fake_chat_db.collection("chat_sessions").document(f"s{i}").set(s.to_dict())

    results = await history.search_conversations("query match", limit=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_large_payload(fake_chat_db):
    """A session with many messages survives the round-trip."""
    from chat_history import ChatHistory, ChatSession

    history = ChatHistory(project_id="test")
    s = ChatSession(
        session_id="big",
        user_id="u0",
        messages=[],
        created_at="2026-06-13T09:00:00",
        updated_at="2026-06-13T09:00:00",
        metadata={},
        status="active",
    )
    for i in range(500):
        s.add_message("user" if i % 2 == 0 else "model", f"msg {i}")
    await history.save_session(s)

    stored = fake_chat_db.collection("chat_sessions").document("big").get()
    assert stored.exists
    assert len(stored.to_dict()["messages"]) == 500

    retrieved = await history.get_session("big")
    assert retrieved is not None
    assert len(retrieved.messages) == 500
    assert retrieved.get_summary() == "250 user messages, 250 AI responses"
