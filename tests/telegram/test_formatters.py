"""Unit tests for ``services.telegram.formatters``.

These tests use plain dicts and ``types.SimpleNamespace`` so they do not depend
on other Telegram subagent modules or on a live database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from services.telegram.formatters import (
    escape_html,
    escape_markdown_legacy,
    escape_markdownv2,
    format_deal_alert,
    format_deal_alert_html,
    format_deal_alert_markdownv2,
    format_deal_alert_plain,
    format_deal_summary,
    format_deal_summary_html,
    format_deal_summary_markdownv2,
    format_deal_summary_plain,
)


@pytest.fixture
def sample_deal():
    """A fully populated deal-shaped object."""
    return SimpleNamespace(
        id="deal-abc-123",
        status="pending",
        buyer_first_name="Jane",
        buyer_last_name="Doe",
        buyer_phone="(281) 324-3020",
        buyer_email="jane.doe+test@example.com",
        salesrep="Alice",
        manufacturer="Tru Belton",
        model="TB-28x60",
        serial_number_1="SN-12345",
        year="2024",
        sales_price=125000.0,
        down_payment=25000.0,
        created_at=datetime(2026, 6, 13, 14, 30, tzinfo=UTC),
    )


@pytest.fixture
def minimal_deal_dict():
    """A sparse deal dict used to test fallback behaviour."""
    return {
        "id": "deal-min",
        "status": "approved",
    }


class TestEscapers:
    """MarkdownV2 and HTML escaping helpers."""

    def test_escape_markdownv2_escapes_reserved_characters(self):
        raw = "_*[]()~`>#+-=|{}.!"
        expected = "\\_\\*\\[\\]\\(\\)\\~\\`\\>\\#\\+\\-\\=\\|\\{\\}\\.\\!"
        assert escape_markdownv2(raw) == expected

    def test_escape_markdownv2_returns_empty_for_none(self):
        assert escape_markdownv2(None) == ""
        assert escape_markdownv2("") == ""

    def test_escape_html_escapes_ampersand_and_brackets(self):
        assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_escape_html_returns_empty_for_none(self):
        assert escape_html(None) == ""

    def test_escape_markdown_legacy_only_escapes_legacy_chars(self):
        raw = "_*[]()~`plus.minus!"
        expected = "\\_\\*\\[\\]\\(\\)\\~\\`plus.minus!"
        assert escape_markdown_legacy(raw) == expected


class TestPlainFormatter:
    def test_includes_status_emoji_and_uppercase_status(self, sample_deal):
        text = format_deal_alert_plain(sample_deal)
        assert text.startswith("⏳ DEAL ALERT — status: PENDING")

    def test_includes_buyer_name_and_home_summary(self, sample_deal):
        text = format_deal_alert_plain(sample_deal)
        assert "Buyer:      Jane Doe" in text
        assert "Home:       2024 Tru Belton TB-28x60 (S/N: SN-12345)" in text

    def test_computes_unpaid_balance(self, sample_deal):
        text = format_deal_alert_plain(sample_deal)
        assert "Unpaid balance: $100,000.00" in text

    def test_emoji_can_be_disabled(self, sample_deal):
        text = format_deal_alert_plain(sample_deal, include_emoji=False)
        assert "⏳" not in text
        assert text.startswith("DEAL ALERT")

    def test_falls_back_gracefully_for_minimal_deal(self, minimal_deal_dict):
        text = format_deal_alert_plain(minimal_deal_dict)
        assert "Buyer:      Unknown buyer" in text
        assert "Sales price:    N/A" in text
        assert "Created: N/A" in text


class TestHtmlFormatter:
    def test_escapes_dynamic_values(self, sample_deal):
        text = format_deal_alert_html(sample_deal)
        # Email contains a plus sign which must not be transformed.
        assert "jane.doe+test@example.com" in text
        assert "<b>Buyer:</b> Jane Doe" in text

    def test_escapes_html_in_user_data(self):
        deal = {"id": "<script>", "status": "pending", "buyer_first_name": "<b>bad</b>"}
        text = format_deal_alert_html(deal)
        assert "<script>" not in text
        assert "&lt;script&gt;" in text
        assert "&lt;b&gt;bad&lt;/b&gt;" in text

    def test_emoji_can_be_disabled(self, sample_deal):
        text = format_deal_alert_html(sample_deal, include_emoji=False)
        assert "⏳" not in text


class TestMarkdownV2Formatter:
    def test_escapes_reserved_characters_in_values(self, sample_deal):
        text = format_deal_alert_markdownv2(sample_deal)
        # Phone contains parentheses and dashes.
        assert "\\(281\\) 324\\-3020" in text
        # Email contains dots and plus.
        assert "jane\\.doe\\+test@example\\.com" in text

    def test_money_escapes_periods_and_commas(self, sample_deal):
        text = format_deal_alert_markdownv2(sample_deal)
        assert "$125,000\\.00" in text

    def test_status_bold_entity_is_valid(self, sample_deal):
        text = format_deal_alert_markdownv2(sample_deal)
        assert "*DEAL ALERT*" in text
        assert "*PENDING*" in text


class TestDispatcher:
    def test_default_is_plain(self, sample_deal):
        text = format_deal_alert(sample_deal)
        assert text.startswith("⏳ DEAL ALERT")

    def test_html_dispatch(self, sample_deal):
        text = format_deal_alert(sample_deal, parse_mode="HTML")
        assert "<b>DEAL ALERT</b>" in text

    def test_markdownv2_dispatch(self, sample_deal):
        text = format_deal_alert(sample_deal, parse_mode="MarkdownV2")
        assert "*DEAL ALERT*" in text

    def test_unknown_parse_mode_falls_back_to_plain(self, sample_deal):
        text = format_deal_alert(sample_deal, parse_mode="MagicMode")
        assert text.startswith("⏳ DEAL ALERT")


class TestSummaryFormatters:
    def test_summary_plain_is_one_line(self, sample_deal):
        text = format_deal_summary_plain(sample_deal)
        assert "\n" not in text
        assert "Jane Doe" in text
        assert "$125,000.00" in text

    def test_summary_markdownv2_escapes_values(self, sample_deal):
        text = format_deal_summary_markdownv2(sample_deal)
        assert "Jane Doe" in text
        assert "$125,000\\.00" in text

    def test_summary_html_escapes_values(self, sample_deal):
        text = format_deal_summary_html(sample_deal)
        assert "&lt;" not in text  # sample has no HTML chars
        assert "$125,000.00" in text

    def test_summary_backward_compatible(self, sample_deal):
        text = format_deal_summary(sample_deal)
        assert "New deal awaiting approval" in text
        assert "*Buyer:* Jane Doe" in text
        assert "*Status:* `pending`" in text
        # Legacy Markdown escaping only escapes _ * [ ] ( ) ~ `; hyphens are left alone.
        assert "*Deal ID:* `deal-abc-123`" in text
