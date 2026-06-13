"""Tests for conversation_memory.py — conversation context extraction with Firestore.

Covers:
  * UserPreferences dataclass round-trip and to_prompt_context
  * ConversationContext dataclass round-trip and update_from_message
  * ConversationMemory Firestore operations: get_context, save_context,
    update_from_interaction, get_context_prompt
  * Edge cases: empty/None values, missing documents, error handling

Run: python -m pytest tests/test_conversation_memory.py -v
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


class FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str):
        return FakeDocumentRef(self._store, doc_id)

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
def fake_memory_db(monkeypatch):
    """Swap conversation_memory's Firestore client for an in-memory fake."""
    import conversation_memory
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
    monkeypatch.setattr(conversation_memory, "firestore", fake_module)
    yield fake


# ─── UserPreferences tests ─────────────────────────────────────────────────


def test_user_preferences_round_trip():
    """UserPreferences serializes and deserializes faithfully."""
    from conversation_memory import UserPreferences

    pref = UserPreferences(
        bedrooms=3,
        bathrooms=2,
        min_budget=50000,
        max_budget=100000,
        home_type="Single Wide",
        preferred_models=["Model A"],
        location_preferences=["Austin, TX"],
    )
    d = pref.to_dict()
    restored = UserPreferences.from_dict(d)
    assert restored.bedrooms == 3
    assert restored.bathrooms == 2
    assert restored.min_budget == 50000
    assert restored.max_budget == 100000
    assert restored.home_type == "Single Wide"
    assert restored.preferred_models == ["Model A"]
    assert restored.location_preferences == ["Austin, TX"]


def test_user_preferences_defaults():
    """None lists are normalized to empty lists in __post_init__."""
    from conversation_memory import UserPreferences

    pref = UserPreferences()
    assert pref.preferred_models == []
    assert pref.location_preferences == []
    assert pref.bedrooms is None


def test_user_preferences_to_prompt_context():
    """to_prompt_context builds a human-readable summary."""
    from conversation_memory import UserPreferences

    pref = UserPreferences(
        bedrooms=3, bathrooms=2, max_budget=120000, home_type="Double Wide"
    )
    ctx = pref.to_prompt_context()
    assert ctx == (
        "User is looking for: 3 bedrooms, 2 bathrooms, "
        "budget up to $120,000, Double Wide homes"
    )


def test_user_preferences_to_prompt_context_singular():
    """Singular bedroom/bathroom uses singular form."""
    from conversation_memory import UserPreferences

    pref = UserPreferences(bedrooms=1, bathrooms=1)
    ctx = pref.to_prompt_context()
    assert "1 bedroom" in ctx
    assert "1 bathroom" in ctx
    assert "budget" not in ctx
    assert "homes" not in ctx


def test_user_preferences_to_prompt_context_empty():
    """Empty preferences yield an empty string."""
    from conversation_memory import UserPreferences

    pref = UserPreferences()
    assert pref.to_prompt_context() == ""


# ─── ConversationContext tests ─────────────────────────────────────────────


def test_conversation_context_round_trip():
    """ConversationContext serializes and deserializes faithfully."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1",
        user_id="u1",
        preferences=UserPreferences(bedrooms=3),
        last_search_query={"model": "X"},
        homes_discussed=["Home A"],
        appointment_intent=True,
        financing_questions=2,
        created_at="2026-06-13T10:00:00",
        updated_at="2026-06-13T10:00:00",
    )
    d = ctx.to_dict()
    restored = ConversationContext.from_dict(d)
    assert restored.session_id == "s1"
    assert restored.preferences.bedrooms == 3
    assert restored.last_search_query == {"model": "X"}
    assert restored.homes_discussed == ["Home A"]
    assert restored.appointment_intent is True
    assert restored.financing_questions == 2


def test_conversation_context_defaults():
    """None lists and timestamps are normalized in __post_init__."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1",
        user_id="u1",
        preferences=UserPreferences(),
        homes_discussed=None,
    )
    assert ctx.homes_discussed == []
    assert ctx.created_at is not None
    assert ctx.updated_at is not None


def test_update_from_message_extracts_bedrooms():
    """update_from_message extracts bedroom count from text."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("I'm looking for a 3 bedroom home")
    assert ctx.preferences.bedrooms == 3


def test_update_from_message_extracts_bedrooms_word():
    """update_from_message extracts bedroom count from number words."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("I need a four bedroom house")
    assert ctx.preferences.bedrooms == 4


def test_update_from_message_extracts_bathrooms():
    """update_from_message extracts bathroom count from text."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("Must have 2 bathrooms")
    assert ctx.preferences.bathrooms == 2


def test_update_from_message_extracts_budget():
    """update_from_message extracts budget from dollar amounts."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("My budget is under $80k")
    assert ctx.preferences.max_budget == 80000.0


def test_update_from_message_extracts_min_budget():
    """update_from_message extracts min budget when 'over' is used."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("I need something over $60k")
    assert ctx.preferences.min_budget == 60000.0


def test_update_from_message_extracts_home_type_single_wide():
    """update_from_message extracts 'Single Wide' from text."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("Do you have any single wide homes?")
    assert ctx.preferences.home_type == "Single Wide"


def test_update_from_message_extracts_home_type_double_wide():
    """update_from_message extracts 'Double Wide' from text."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("Show me double wide options")
    assert ctx.preferences.home_type == "Double Wide"


def test_update_from_message_extracts_home_type_modular():
    """update_from_message extracts 'Modular' from text."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("I prefer modular homes")
    assert ctx.preferences.home_type == "Modular"


def test_update_from_message_extracts_appointment_intent():
    """update_from_message sets appointment_intent when scheduling words appear."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("Can I schedule a tour?")
    assert ctx.appointment_intent is True


def test_update_from_message_extracts_financing():
    """update_from_message increments financing_questions for finance words."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("What are the monthly payments?")
    assert ctx.financing_questions == 1
    ctx.update_from_message("What is the interest rate?")
    assert ctx.financing_questions == 2


def test_update_from_message_tracks_search_results():
    """update_from_message adds home models from search_results."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message(
        "I like this one",
        search_results=[{"model": "Home A"}, {"model": "Home B"}, {"model": "Home C"}],
    )
    assert ctx.homes_discussed == ["Home A", "Home B", "Home C"]


def test_update_from_message_dedupes_search_results():
    """Duplicate models are not added twice."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("I like this one", search_results=[{"model": "Home A"}])
    ctx.update_from_message("I still like it", search_results=[{"model": "Home A"}])
    assert ctx.homes_discussed == ["Home A"]


def test_update_from_message_limits_search_results():
    """Only top 3 search results are tracked."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message(
        "I like these",
        search_results=[
            {"model": "A"},
            {"model": "B"},
            {"model": "C"},
            {"model": "D"},
        ],
    )
    assert ctx.homes_discussed == ["A", "B", "C"]


def test_update_from_message_no_match():
    """Messages without keywords do not mutate preferences."""
    from conversation_memory import ConversationContext, UserPreferences

    ctx = ConversationContext(
        session_id="s1", user_id="u1", preferences=UserPreferences()
    )
    ctx.update_from_message("Hello")
    assert ctx.preferences.bedrooms is None
    assert ctx.preferences.bathrooms is None
    assert ctx.preferences.max_budget is None
    assert ctx.preferences.home_type is None
    assert ctx.appointment_intent is False
    assert ctx.financing_questions == 0
    assert ctx.homes_discussed == []


# ─── ConversationMemory tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_context_existing(fake_memory_db):
    """get_context returns the persisted document when it exists."""
    from conversation_memory import ConversationContext, ConversationMemory, UserPreferences

    ctx = ConversationContext(
        session_id="s1",
        user_id="u1",
        preferences=UserPreferences(bedrooms=3),
        created_at="2026-06-13T10:00:00",
        updated_at="2026-06-13T10:00:00",
    )
    fake_memory_db.collection("conversation_contexts").document("s1").set(ctx.to_dict())

    memory = ConversationMemory(project_id="test")
    result = await memory.get_context("s1", "u1")
    assert result.session_id == "s1"
    assert result.preferences.bedrooms == 3


@pytest.mark.asyncio
async def test_get_context_new(fake_memory_db):
    """get_context creates a fresh context when the document is missing."""
    from conversation_memory import ConversationMemory

    memory = ConversationMemory(project_id="test")
    result = await memory.get_context("s2", "u2")
    assert result.session_id == "s2"
    assert result.user_id == "u2"
    assert result.preferences.bedrooms is None
    assert result.homes_discussed == []


@pytest.mark.asyncio
async def test_save_context(fake_memory_db):
    """save_context writes the full document."""
    from conversation_memory import ConversationContext, ConversationMemory, UserPreferences

    memory = ConversationMemory(project_id="test")
    ctx = ConversationContext(
        session_id="s3",
        user_id="u3",
        preferences=UserPreferences(bathrooms=2),
        created_at="2026-06-13T10:00:00",
        updated_at="2026-06-13T10:00:00",
    )
    await memory.save_context(ctx)

    stored = fake_memory_db.collection("conversation_contexts").document("s3").get()
    assert stored.exists
    assert stored.to_dict()["preferences"]["bathrooms"] == 2


@pytest.mark.asyncio
async def test_update_from_interaction(fake_memory_db):
    """update_from_interaction updates context and persists it."""
    from conversation_memory import ConversationMemory

    memory = ConversationMemory(project_id="test")
    ctx = await memory.update_from_interaction(
        "s4",
        "u4",
        "I need 3 bedrooms",
        search_results=[{"model": "Home X"}],
    )
    assert ctx.preferences.bedrooms == 3
    assert ctx.homes_discussed == ["Home X"]

    stored = fake_memory_db.collection("conversation_contexts").document("s4").get()
    assert stored.exists
    assert stored.to_dict()["preferences"]["bedrooms"] == 3


@pytest.mark.asyncio
async def test_get_context_prompt(fake_memory_db):
    """get_context_prompt returns a formatted string with all context."""
    from conversation_memory import ConversationContext, ConversationMemory, UserPreferences

    memory = ConversationMemory(project_id="test")
    ctx = ConversationContext(
        session_id="s5",
        user_id="u5",
        preferences=UserPreferences(
            bedrooms=3, bathrooms=2, max_budget=100000, home_type="Modular"
        ),
        homes_discussed=["Home A", "Home B"],
        appointment_intent=True,
        financing_questions=1,
        created_at="2026-06-13T10:00:00",
        updated_at="2026-06-13T10:00:00",
    )
    fake_memory_db.collection("conversation_contexts").document("s5").set(ctx.to_dict())

    prompt = await memory.get_context_prompt("s5", "u5")
    assert "Conversation Context:" in prompt
    assert "3 bedrooms" in prompt
    assert "2 bathrooms" in prompt
    assert "budget up to $100,000" in prompt
    assert "Modular homes" in prompt
    assert "Previously discussed: Home A, Home B" in prompt
    assert "scheduling an appointment" in prompt
    assert "1 financing question(s)" in prompt


@pytest.mark.asyncio
async def test_get_context_prompt_empty(fake_memory_db):
    """get_context_prompt returns empty string when no preferences or context."""
    from conversation_memory import ConversationContext, ConversationMemory, UserPreferences

    memory = ConversationMemory(project_id="test")
    ctx = ConversationContext(
        session_id="s6",
        user_id="u6",
        preferences=UserPreferences(),
        created_at="2026-06-13T10:00:00",
        updated_at="2026-06-13T10:00:00",
    )
    fake_memory_db.collection("conversation_contexts").document("s6").set(ctx.to_dict())

    prompt = await memory.get_context_prompt("s6", "u6")
    assert prompt == ""


@pytest.mark.asyncio
async def test_get_context_prompt_error(fake_memory_db):
    """get_context_prompt swallows errors and returns empty string."""
    from conversation_memory import ConversationMemory

    memory = ConversationMemory(project_id="test")
    # Force an error by corrupting the stored data so from_dict fails
    fake_memory_db.collection("conversation_contexts").document("s7").set({"bad": "data"})

    prompt = await memory.get_context_prompt("s7", "u7")
    assert prompt == ""
