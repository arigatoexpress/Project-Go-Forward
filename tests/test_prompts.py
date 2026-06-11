"""Tests for the prompt template system (prompt_loader.py + prompts/*.md)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import prompt_loader
from prompt_loader import available_prompts, build_context, render_prompt


def test_all_templates_render_without_unresolved_variables():
    names = available_prompts()
    assert {"root_agent", "sales_agent", "service_agent"} <= set(names)
    for name in names:
        rendered = render_prompt(name)
        assert len(rendered) > 200, name
        assert "$business" not in rendered, name
        assert "${" not in rendered, name


def test_business_values_come_from_config():
    for name in available_prompts():
        rendered = render_prompt(name)
        assert "Texas Home Outlet" in rendered, name


def test_sales_prompt_keeps_property_card_contract():
    """The UI parses ```property blocks — the instruction must keep teaching it."""
    rendered = render_prompt("sales_agent")
    assert "```property" in rendered
    assert '"display_price": "$XX,XXX"' in rendered  # $$ escape renders one $
    assert '"gallery_images"' in rendered
    assert "save_lead" in rendered


def test_sales_prompt_has_grounding_and_privacy_rules():
    rendered = render_prompt("sales_agent")
    assert "Never invent" in rendered
    assert "Social Security" in rendered


def test_service_prompt_has_safety_triage():
    rendered = render_prompt("service_agent")
    assert "911" in rendered
    assert "generate_service_ticket" in rendered


def test_dates_render_fresh():
    context = build_context()
    assert context["today_iso"].count("-") == 2
    assert context["today_str"] in render_prompt("sales_agent")


def test_unknown_variable_fails_loudly(tmp_path, monkeypatch):
    bad = tmp_path / "bad_prompt.md"
    bad.write_text("Hello $business_name, your $made_up_variable is here.")
    monkeypatch.setenv("THO_PROMPT_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="made_up_variable"):
        render_prompt("bad_prompt")


def test_prompt_dir_override_and_extra_variables(tmp_path, monkeypatch):
    override = tmp_path / "sales_agent.md"
    override.write_text("Override for $business_name with $custom_var.")
    monkeypatch.setenv("THO_PROMPT_DIR", str(tmp_path))
    prompt_loader._template_cache.clear()
    try:
        rendered = render_prompt("sales_agent", custom_var="hello")
        assert rendered == "Override for Texas Home Outlet with hello."
    finally:
        prompt_loader._template_cache.clear()


def test_root_agent_builds_with_template_prompts(monkeypatch):
    """The ADK agents must construct with rendered instructions."""
    import root_agent as ra

    agent = ra._create_root_agent()
    assert agent.name == "root_agent"
    assert "Front Desk" in agent.instruction
    names = {sub.name for sub in agent.sub_agents}
    assert names == {"sales_agent", "service_agent"}
    sales = next(s for s in agent.sub_agents if s.name == "sales_agent")
    assert "```property" in sales.instruction
