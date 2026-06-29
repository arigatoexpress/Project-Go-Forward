"""Regression coverage for THO public hours.

Staff confirmed the showroom is closed on Sunday. The value is surfaced through
React constants, config/SEO, appointment slots, and the AI helper tools, so pin
the cross-layer behavior in one small file.
"""

from __future__ import annotations

import sys
import types
from datetime import timedelta
from importlib import import_module
from pathlib import Path

import yaml

import config_loader
from appointment_manager import TIMEZONE, _get_hours_for_date

google_adk = types.ModuleType("google.adk")
google_adk_tools = types.ModuleType("google.adk.tools")
google_adk_tools.ToolContext = object
google_adk.tools = google_adk_tools
sys.modules.setdefault("google.adk", google_adk)
sys.modules.setdefault("google.adk.tools", google_adk_tools)

REPO = Path(__file__).resolve().parent.parent


def _crm_tools():
    return import_module("tools.crm_tools")


def _next_sunday():
    crm_tools = _crm_tools()
    today = crm_tools.datetime.now(TIMEZONE).date()
    days_ahead = (6 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def test_config_and_structured_hours_mark_sunday_closed():
    config = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    business = config["business"]

    assert business["hours"]["sunday"] == "Sun Closed"
    assert "Sun Closed" in config_loader.business_hours()

    structured_days = {day for block in business["hours_structured"] for day in block["days"]}
    assert "Sunday" not in structured_days


def test_frontend_business_hours_constant_marks_sunday_closed():
    constants = (REPO / "frontend/src/constants.js").read_text(encoding="utf-8")

    assert 'BUSINESS_HOURS = "Mon-Fri 9-6, Sat 9-5, Sun Closed"' in constants
    assert "Sun 12-3" not in constants


def test_booking_layers_treat_sunday_as_closed():
    crm_tools = _crm_tools()
    sunday = _next_sunday()

    assert _get_hours_for_date(sunday) is None

    slots = crm_tools.check_available_slots(sunday.isoformat())
    assert slots["day_name"] == "Sunday"
    assert slots["available_slots"] == []
    assert "closed" in slots["message"].lower()


def test_ai_business_hours_tool_reports_sunday_closed():
    crm_tools = _crm_tools()
    hours = crm_tools.get_business_hours()["hours"]

    assert "Sun: Closed" in hours
    assert "Sun: 12pm-3pm" not in hours
