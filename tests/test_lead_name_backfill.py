"""
Unit tests for tools/lead_name_backfill.py name-extraction logic.

All tests are offline — no Firestore, no LLM calls.
"""

import sys
import os
import importlib.util

# Load the module directly to avoid tools/__init__.py pulling in pypdf.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "lead_name_backfill.py")
_spec = importlib.util.spec_from_file_location("lead_name_backfill", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

extract_name_from_messages = _mod.extract_name_from_messages
_is_unknown_name = _mod._is_unknown_name
_messages_to_dicts = _mod._messages_to_dicts
LeadNamePatch = _mod.LeadNamePatch


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _user(text: str) -> dict:
    return {"role": "user", "text": text}


def _agent(text: str) -> dict:
    return {"role": "model", "text": text}


# ──────────────────────────────────────────────────────────────────────────────
# Edge-case 1: No name mentioned anywhere in the conversation
# ──────────────────────────────────────────────────────────────────────────────

class TestNoNameMentioned:
    def test_empty_messages_returns_none(self):
        assert extract_name_from_messages([]) is None

    def test_generic_chat_no_introduction(self):
        msgs = [
            _user("Hi, I'm looking for a 3-bedroom home under $80k."),
            _agent("Great! We have several options. What area do you prefer?"),
            _user("Somewhere in the Houston area would be ideal."),
        ]
        result = extract_name_from_messages(msgs)
        assert result is None, f"Expected None but got {result}"

    def test_pronouns_only_not_extracted_as_names(self):
        msgs = [
            _user("I am looking for a home. They told me to contact you."),
            _user("She said you have good deals. He recommended you."),
        ]
        result = extract_name_from_messages(msgs)
        assert result is None, f"Pronouns should not be extracted as names, got {result}"

    def test_agent_messages_ignored(self):
        # Name appears only in agent (model) messages — should not be picked up
        msgs = [
            _agent("Hi John, how can I help you today?"),
            _user("I just want to browse available homes."),
        ]
        result = extract_name_from_messages(msgs)
        assert result is None, "Agent messages should not be scanned for names"

    def test_common_words_not_names(self):
        msgs = [
            _user("My name is Texas Home Outlet!"),  # stop words in name slot
            _user("I am Looking for a new home."),   # stop word "Looking"
        ]
        result = extract_name_from_messages(msgs)
        assert result is None, f"Stop words should be rejected, got {result}"


# ──────────────────────────────────────────────────────────────────────────────
# Edge-case 2: Multiple names mentioned — first strongest match wins
# ──────────────────────────────────────────────────────────────────────────────

class TestMultipleNames:
    def test_first_high_confidence_match_returned(self):
        msgs = [
            _user("Hi, my name is Sarah Johnson. I'm also here with Mike Davis."),
        ]
        result = extract_name_from_messages(msgs)
        assert result is not None
        name, confidence, excerpt = result
        assert name == "Sarah Johnson", f"Expected 'Sarah Johnson', got '{name}'"
        assert confidence >= 0.90

    def test_name_in_first_message_preferred_over_later(self):
        msgs = [
            _user("Hello, I'm Alice Brown. Nice to meet you."),
            _user("My name is Bob White actually."),
        ]
        # First match in iteration order wins (Alice from first message)
        result = extract_name_from_messages(msgs)
        assert result is not None
        name, confidence, _ = result
        assert name == "Alice Brown", f"Expected 'Alice Brown' from first msg, got '{name}'"

    def test_highest_confidence_pattern_chosen_within_message(self):
        # "my name is" (0.95) and "I'm" (0.88) both appear; "my name is" should win
        msgs = [
            _user("Hey, I'm just browsing. But my name is Carlos Rivera if you need it."),
        ]
        result = extract_name_from_messages(msgs)
        assert result is not None
        name, confidence, _ = result
        # The first pattern to match wins; "I'm" comes first in text but "my name is"
        # has a higher pattern confidence. Because we iterate patterns in confidence order,
        # "my name is" (0.95) is tried before "I'm" (0.88).
        assert name == "Carlos Rivera"
        assert confidence == 0.95

    def test_three_word_name_captured(self):
        msgs = [_user("My name is Mary Jane Watson.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        name, _, _ = result
        assert name == "Mary Jane Watson"


# ──────────────────────────────────────────────────────────────────────────────
# Edge-case 3: Pronoun / generic word patterns that look like names
# ──────────────────────────────────────────────────────────────────────────────

class TestPronounAndStopWordGuards:
    def test_i_am_not_extracted(self):
        msgs = [_user("I am interested in a double-wide.")]
        result = extract_name_from_messages(msgs)
        assert result is None

    def test_call_me_stop_word(self):
        msgs = [_user("Call me anytime, thanks!")]
        result = extract_name_from_messages(msgs)
        assert result is None  # "Anytime" not in stop set but is short/not a name

    def test_this_is_generic(self):
        # "This is" pattern — "Texas" is in _STOP_NAMES
        msgs = [_user("This is Texas Home Outlet?")]
        result = extract_name_from_messages(msgs)
        assert result is None

    def test_single_char_initial_rejected(self):
        # Single-letter "names" like "I'm M looking" shouldn't match
        msgs = [_user("I'm M looking for a home.")]
        result = extract_name_from_messages(msgs)
        assert result is None

    def test_valid_name_not_suppressed_by_stop_check(self):
        # Make sure legitimate names with no stop words still pass
        msgs = [_user("Hi, I'm Jennifer Lopez.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        name, confidence, _ = result
        assert name == "Jennifer Lopez"
        assert confidence >= 0.88


# ──────────────────────────────────────────────────────────────────────────────
# Positive extraction patterns
# ──────────────────────────────────────────────────────────────────────────────

class TestPositiveExtractionPatterns:
    def test_my_name_is(self):
        msgs = [_user("My name is John Smith.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        assert result[0] == "John Smith"
        assert result[1] == 0.95

    def test_my_names_contraction(self):
        msgs = [_user("My name's Rachel Green by the way.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        assert result[0] == "Rachel Green"

    def test_hi_im(self):
        msgs = [_user("Hi, I'm David Chen. Looking for a home.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        assert result[0] == "David Chen"

    def test_hello_i_am(self):
        msgs = [_user("Hello, I am Maria Santos.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        assert result[0] == "Maria Santos"

    def test_this_is_pattern(self):
        msgs = [_user("This is Robert Kim calling about a home.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        assert result[0] == "Robert Kim"

    def test_name_is_pattern(self):
        msgs = [_user("The name is Williams, Tom Williams.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        assert "Williams" in result[0]

    def test_excerpt_included(self):
        msgs = [_user("My name is Anna Lee and I want to buy a home.")]
        result = extract_name_from_messages(msgs)
        assert result is not None
        _, _, excerpt = result
        assert excerpt  # excerpt must not be empty
        assert len(excerpt) <= 200


# ──────────────────────────────────────────────────────────────────────────────
# _is_unknown_name helper
# ──────────────────────────────────────────────────────────────────────────────

class TestIsUnknownName:
    def test_none(self):
        assert _is_unknown_name(None) is True

    def test_empty_string(self):
        assert _is_unknown_name("") is True

    def test_whitespace(self):
        assert _is_unknown_name("   ") is True

    def test_unknown_literal(self):
        assert _is_unknown_name("Unknown") is True
        assert _is_unknown_name("UNKNOWN") is True
        assert _is_unknown_name("unknown") is True

    def test_anonymous(self):
        assert _is_unknown_name("anonymous") is True

    def test_guest(self):
        assert _is_unknown_name("Guest") is True

    def test_real_name(self):
        assert _is_unknown_name("John Smith") is False

    def test_partial_name(self):
        assert _is_unknown_name("Sarah") is False


# ──────────────────────────────────────────────────────────────────────────────
# _messages_to_dicts normalisation
# ──────────────────────────────────────────────────────────────────────────────

class TestMessagesToDicts:
    def test_dict_input_passthrough(self):
        raw = [{"role": "user", "text": "hello"}]
        result = _messages_to_dicts(raw)
        assert result == [{"role": "user", "text": "hello"}]

    def test_object_input_normalised(self):
        class FakeMsg:
            def __init__(self, role, text):
                self.role = role
                self.text = text

        msgs = [FakeMsg("user", "hi"), FakeMsg("model", "hello")]
        result = _messages_to_dicts(msgs)
        assert result[0] == {"role": "user", "text": "hi"}
        assert result[1] == {"role": "model", "text": "hello"}


# ──────────────────────────────────────────────────────────────────────────────
# LeadNamePatch Pydantic model
# ──────────────────────────────────────────────────────────────────────────────

class TestLeadNamePatch:
    def test_serialises_to_jsonl(self):
        patch = LeadNamePatch(
            lead_id="lead_abc",
            current_name="Unknown",
            proposed_name="John Smith",
            source_session_id="sess_xyz",
            source_message_excerpt="...my name is John Smith...",
            confidence=0.95,
        )
        line = patch.model_dump_json()
        import json
        data = json.loads(line)
        assert data["lead_id"] == "lead_abc"
        assert data["proposed_name"] == "John Smith"
        assert data["confidence"] == 0.95
        assert "extracted_at" in data

    def test_confidence_bounds_validated(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LeadNamePatch(
                lead_id="x",
                current_name=None,
                proposed_name="Bob",
                source_session_id="s",
                source_message_excerpt="",
                confidence=1.5,  # out of range
            )
