"""Test that the booking fix is correctly applied.

The no-op book_appointment and cancel_appointment tools in crm_tools.py were
removed from the sales agent's tool list in root_agent.py to prevent the AI
from falsely confirming appointments that are never actually written to the
calendar. This test verifies the fix is in place.
"""

import sys
from pathlib import Path

# Ensure root_agent can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestBookingFix:
    """Verify the AI chat agent no longer has broken booking tools."""

    def test_sales_agent_does_not_have_book_appointment_tool(self):
        """The sales agent must NOT have the no-op book_appointment tool."""
        from root_agent import _create_sales_agent

        agent = _create_sales_agent()
        tool_names = {t.__name__ for t in (agent.tools or [])}
        assert "book_appointment" not in tool_names, (
            "book_appointment is a no-op that falsely confirms appointments. "
            "It must be removed from the sales agent's tools."
        )

    def test_sales_agent_does_not_have_cancel_appointment_tool(self):
        """The sales agent must NOT have the no-op cancel_appointment tool."""
        from root_agent import _create_sales_agent

        agent = _create_sales_agent()
        tool_names = {t.__name__ for t in (agent.tools or [])}
        assert "cancel_appointment" not in tool_names, (
            "cancel_appointment is a no-op that falsely claims cancellation. "
            "It must be removed from the sales agent's tools."
        )

    def test_sales_agent_prompt_does_not_instruct_booking_tool(self):
        """The sales agent prompt must not instruct the AI to use book_appointment."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "sales_agent.md"
        prompt_text = prompt_path.read_text()
        assert "book_appointment" not in prompt_text, (
            "The sales agent prompt must not reference the removed book_appointment tool. "
            "Update the prompt to direct users to the website booking page instead."
        )

    def test_sales_agent_prompt_redirects_to_website(self):
        """The sales agent prompt must direct users to the website booking page."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "sales_agent.md"
        prompt_text = prompt_path.read_text()
        assert "texashomeoutlet.com/appointments" in prompt_text, (
            "The sales agent prompt must direct customers to the website booking page "
            "to prevent false confirmations from the no-op booking tool."
        )

    def test_crm_tools_no_op_functions_still_exist_for_backwards_compat(self):
        """The no-op functions remain in crm_tools.py for backwards compatibility
        but are NOT wired into the agent."""
        from tools import crm_tools

        assert hasattr(crm_tools, "book_appointment")
        assert hasattr(crm_tools, "cancel_appointment")
        # Verify they are still no-ops (don't write to Firestore)
        result = crm_tools.book_appointment(
            "Test User", "555-010-0000", "2099-01-01", "10:00 AM"
        )
        assert result["success"] is True
        # The "confirmation" is fake — no actual booking occurred
        assert "confirmed" in result["message"].lower()

    def test_sales_agent_has_remaining_tools(self):
        """The sales agent still has the useful tools after the removal."""
        from root_agent import _create_sales_agent

        agent = _create_sales_agent()
        tool_names = {t.__name__ for t in (agent.tools or [])}
        expected = {
            "search_inventory",
            "get_current_datetime",
            "check_available_slots",
            "get_business_hours",
            "save_lead",
        }
        assert expected <= tool_names, (
            f"Expected tools {expected} missing from agent. Got: {tool_names}"
        )
