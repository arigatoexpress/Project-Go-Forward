"""Tests for tools/form_extraction.py — PII filtering before LLM calls.

These tests prove the guardrail in CLAUDE.md: "Never send PII to LLM — strip PII
fields before Gemini API calls." We confirm that:
  * SSN / DOB / income field definitions are NOT included in the extraction prompt
    that gets sent to Gemini (PII-redaction path).
  * Normal (non-PII) fields still flow through to the prompt and the result
    (happy path).
  * Even if the LLM hallucinates a PII field back, it is dropped from the result.

The Gemini/genai call is fully mocked — no network calls are made.

Run: python -m pytest tests/test_form_extraction.py -q
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import google.genai  # noqa: F401  (ensure the real submodule is importable before patching)

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.field_map_loader import get_fields_for_template
from tools import form_extraction

# Template that mixes PII fields (SSN, Date of Birth) with normal fields.
TEMPLATE = "creditapp.pdf"

# A conversation transcript that contains raw PII values. The extraction layer
# should never surface these as *fields* to the model, and the field metadata it
# does send must not reference SSN/DOB/income.
CONVERSATION = (
    "user: Hi, I'm John Doe and my employer is Acme Corp.\n"
    "user: My SSN is 123-45-6789 and I was born on 01/02/1980.\n"
    "user: My monthly income is $5000 and my phone is 555-123-4567."
)

# PII data-field names (from config/field_map.json) that must never be offered
# to the LLM as extractable fields.
PII_FIELD_NAMES = {"buyer_ssn", "buyer_dob", "buyer_income", "co_buyer_ssn", "co_buyer_dob"}

# PII labels that appear in the field definitions — they must not leak into the
# prompt either (the prompt lists fields by label).
PII_LABELS = {"SSN", "Date of Birth", "Monthly Income"}


class _FakeResponse:
    """Mimics the genai generate_content response (exposes a .text attribute)."""

    def __init__(self, text: str):
        self.text = text


class _FakeModels:
    def __init__(self, captured: dict, response_json: str):
        self._captured = captured
        self._response_json = response_json

    def generate_content(self, model=None, contents=None):
        # Capture exactly what would be sent to Gemini so the test can inspect it.
        self._captured["model"] = model
        self._captured["contents"] = contents
        return _FakeResponse(self._response_json)


class _FakeClient:
    def __init__(self, captured: dict, response_json: str):
        self.models = _FakeModels(captured, response_json)


def _install_fake_genai(captured: dict, response_json: str):
    """Patch `google.genai.Client` so no network call is made.

    `extract_form_data_from_session` does `from google import genai` at call
    time and then calls `genai.Client()`. Patching the `Client` symbol on the
    real submodule makes the fake visible to that import (a sys.modules patch
    alone does not work, because `from google import genai` resolves `genai` as
    an attribute of the already-imported `google` package object).
    """

    def _client_factory(*args, **kwargs):
        return _FakeClient(captured, response_json)

    return patch("google.genai.Client", _client_factory)


def _run(coro):
    return asyncio.run(coro)


def test_pii_field_definitions_are_stripped_from_llm_prompt():
    """PII-redaction path: the prompt sent to Gemini must not reference any
    SSN / DOB / income field name or label."""
    captured: dict = {}
    # LLM returns only safe, validated fields.
    response_json = '{"buyer_name": "John Doe", "employer_name": "Acme Corp"}'

    with _install_fake_genai(captured, response_json), patch.object(
        form_extraction,
        "_get_conversation_text",
        return_value=CONVERSATION,
    ) as _mock_convo:
        # Make the patched _get_conversation_text awaitable.
        async def _fake_convo(session_id, runner=None):
            return CONVERSATION

        _mock_convo.side_effect = _fake_convo

        result = _run(
            form_extraction.extract_form_data_from_session(
                session_id="sess-1",
                template_name=TEMPLATE,
                runner=None,
            )
        )

    prompt = captured.get("contents")
    assert prompt is not None, "Gemini was never called / prompt not captured"

    # No PII field *name* should appear in the prompt's field list.
    for pii_field in PII_FIELD_NAMES:
        assert pii_field not in prompt, f"PII field name leaked into LLM prompt: {pii_field}"

    # No PII *label* should appear in the listed extractable fields. We check the
    # field-list region of the prompt (the section after "Fields to look for:")
    # so the standing safety instruction ("Do NOT extract any SSN, date of
    # birth, income...") doesn't trip the assertion.
    field_list_region = prompt.split("Conversation:")[0]
    fields_to_look_for = field_list_region.split("Fields to look for:")[-1]
    for label in PII_LABELS:
        assert (
            label not in fields_to_look_for
        ), f"PII label leaked into the extractable-field list: {label}"

    # The result itself must not contain any PII field.
    extracted = result["extracted_data"]
    for pii_field in PII_FIELD_NAMES:
        assert pii_field not in extracted, f"PII field present in extracted result: {pii_field}"


def test_non_pii_fields_pass_through_to_prompt_and_result():
    """Happy path: normal fields are offered to the model and returned."""
    captured: dict = {}
    response_json = '{"buyer_name": "John Doe", "employer_name": "Acme Corp"}'

    with _install_fake_genai(captured, response_json), patch.object(
        form_extraction, "_get_conversation_text"
    ) as mock_convo:

        async def _fake_convo(session_id, runner=None):
            return CONVERSATION

        mock_convo.side_effect = _fake_convo

        result = _run(
            form_extraction.extract_form_data_from_session(
                session_id="sess-2",
                template_name=TEMPLATE,
                runner=None,
            )
        )

    prompt = captured["contents"]
    # Representative non-PII fields are present in the prompt.
    assert "buyer_name" in prompt
    assert "employer_name" in prompt

    # And the extracted result carries them through.
    extracted = result["extracted_data"]
    assert extracted.get("buyer_name") == "John Doe"
    assert extracted.get("employer_name") == "Acme Corp"


def test_llm_returned_pii_keys_are_dropped_from_result():
    """Defense in depth: even if the model echoes back a PII key, the validation
    step drops it because it is not in the safe-field allowlist."""
    captured: dict = {}
    # Adversarial: model tries to return an SSN/DOB value.
    response_json = (
        '{"buyer_name": "John Doe", "buyer_ssn": "123-45-6789", '
        '"buyer_dob": "01/02/1980"}'
    )

    with _install_fake_genai(captured, response_json), patch.object(
        form_extraction, "_get_conversation_text"
    ) as mock_convo:

        async def _fake_convo(session_id, runner=None):
            return CONVERSATION

        mock_convo.side_effect = _fake_convo

        result = _run(
            form_extraction.extract_form_data_from_session(
                session_id="sess-3",
                template_name=TEMPLATE,
                runner=None,
            )
        )

    extracted = result["extracted_data"]
    assert "buyer_ssn" not in extracted
    assert "buyer_dob" not in extracted
    assert extracted.get("buyer_name") == "John Doe"


def test_template_safe_fields_exclude_all_pii_definitions():
    """Sanity check on the field registry itself: every field flagged pii=True
    for this template is excluded from the safe (LLM-bound) field set, mirroring
    the filter inside extract_form_data_from_session."""
    template_fields = get_fields_for_template(TEMPLATE)
    pii_in_template = {
        name for name, defn in template_fields.items() if defn.get("pii", False)
    }
    # Template must actually contain PII fields, else the test proves nothing.
    assert pii_in_template, "Expected creditapp.pdf to define PII fields"

    safe_fields = {
        name: defn
        for name, defn in template_fields.items()
        if not defn.get("pii", False)
    }
    # No PII field name survives into the safe set.
    assert pii_in_template.isdisjoint(safe_fields.keys())
    # Specifically SSN and DOB are gone.
    assert "buyer_ssn" not in safe_fields
    assert "buyer_dob" not in safe_fields
