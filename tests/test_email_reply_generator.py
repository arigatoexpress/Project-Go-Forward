"""Tests for email_reply_generator.py — fixed-template reply rendering.

Safety properties under test (these ARE the spec):

1. The acknowledgement template contains ZERO content derived from the email
   body — the render function does not even accept a body argument, and the
   output never echoes anything but a sanitized subject line.
2. Deterministic: byte-identical output for the same input. No LLM, no
   network, no clock in the safe path.
3. Subject is HTML-escaped and header-sanitized (no CRLF injection, no markup
   pass-through).

Run: python -m pytest tests/test_email_reply_generator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from email_reply_generator import (  # noqa: E402
    draft_substantive_body,
    render_ack,
)

# ─── Acknowledgement template ───────────────────────────────────────────────


class TestRenderAck:
    def test_returns_subject_html_and_text(self):
        result = render_ack("Interested in a home")
        assert set(result) == {"subject", "html", "text"}
        assert result["subject"].startswith("Re: ")
        assert "Texas Home Outlet" in result["html"]
        assert "Texas Home Outlet" in result["text"]

    def test_deterministic_byte_identical(self):
        a = render_ack("Hello there")
        b = render_ack("Hello there")
        assert a == b

    def test_subject_is_html_escaped(self):
        result = render_ack('<script>alert("x")</script>')
        assert "<script>" not in result["html"]
        assert "&lt;script&gt;" in result["html"]

    def test_subject_header_crlf_stripped(self):
        result = render_ack("Hi\r\nBcc: attacker@evil.com")
        assert "\r" not in result["subject"]
        assert "\n" not in result["subject"]

    def test_subject_length_capped(self):
        result = render_ack("x" * 5000)
        assert len(result["subject"]) < 300

    def test_empty_subject_still_renders(self):
        result = render_ack("")
        assert result["subject"]
        assert "Texas Home Outlet" in result["html"]

    def test_non_string_subject_degrades(self):
        result = render_ack(None)
        assert result["subject"]
        assert "None" not in result["html"]

    def test_ack_promises_human_follow_up_and_no_answers(self):
        """The ack may only say 'we received it, a person will follow up' —
        it must never look like an answer to the inquiry."""
        result = render_ack("What are your hours?")
        text = result["text"].lower()
        assert "received" in text
        assert "team" in text or "follow up" in text
        # No business specifics that could constitute a wrong 'answer'.
        for forbidden in ("price", "$", "warranty", "escrow"):
            assert forbidden not in text

    def test_ack_never_accepts_body_content(self):
        """API-level guarantee: there is no body parameter to echo."""
        import inspect

        params = inspect.signature(render_ack).parameters
        assert "body" not in params
        assert "content" not in params


# ─── Substantive draft skeleton ─────────────────────────────────────────────


class TestDraftSubstantiveBody:
    def test_returns_editable_skeleton(self):
        body = draft_substantive_body(rule_hits=["trigger:money"])
        assert "[" in body  # contains an operator-edit placeholder
        assert "Texas Home Outlet" in body

    def test_mentions_triage_context_labels_only(self):
        body = draft_substantive_body(rule_hits=["trigger:money", "trigger:legal"])
        assert "money" in body
        assert "legal" in body

    def test_deterministic(self):
        assert draft_substantive_body(rule_hits=["a"]) == draft_substantive_body(rule_hits=["a"])

    def test_handles_empty_hits(self):
        body = draft_substantive_body(rule_hits=[])
        assert body
