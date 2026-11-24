"""Thanksgiving 2026 hours requested by THO staff (L.A., 2026-11-18).

Wed 11/25 9:00-12:00; closed Thu 11/26 - Sat 11/28; normal hours Mon 11/30.
Pins the same schedule across config.yaml, appointment slots, and the
LocalBusiness JSON-LD so the layers cannot drift apart.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from appointment_manager import _generate_slots, _get_hours_for_date

REPO = Path(__file__).resolve().parent.parent


def _special_hours() -> dict:
    config = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    return {s["date"]: s for s in config["business"]["special_hours"]}


def test_config_special_hours_match_staff_schedule():
    special = _special_hours()
    assert special["2026-11-25"]["opens"] == "09:00"
    assert special["2026-11-25"]["closes"] == "12:00"
    for closed in ("2026-11-26", "2026-11-27", "2026-11-28"):
        assert special[closed]["opens"] == special[closed]["closes"] == "00:00"


def test_appointment_slots_follow_holiday_schedule():
    assert _get_hours_for_date(date(2026, 11, 25)) == (9, 12)
    assert _generate_slots(9, 12) == ["9:00 AM", "10:00 AM", "11:00 AM"]
    for closed in (26, 27, 28):
        assert _get_hours_for_date(date(2026, 11, closed)) is None
    # Normal weekly schedule resumes Monday 11/30; Sunday stays closed.
    assert _get_hours_for_date(date(2026, 11, 29)) is None
    assert _get_hours_for_date(date(2026, 11, 30)) == _get_hours_for_date(date(2026, 11, 23))
    assert _get_hours_for_date(date(2026, 11, 30)) is not None


def test_jsonld_emits_special_opening_hours():
    import seo_routes

    data = seo_routes._local_business_jsonld()
    specs = {s["validFrom"]: s for s in data["specialOpeningHoursSpecification"]}
    assert specs["2026-11-25"]["opens"] == "09:00"
    assert specs["2026-11-25"]["closes"] == "12:00"
    assert specs["2026-11-26"]["opens"] == specs["2026-11-26"]["closes"] == "00:00"
    assert all(s["validFrom"] == s["validThrough"] for s in specs.values())


def test_closure_banner_constant_matches_config():
    js = (REPO / "frontend/src/constants.js").read_text(encoding="utf-8")
    block = js[js.index("export const STORE_CLOSURE") :]
    block = block[: block.index("};")]
    assert re.search(r"start: '2026-11-25'", block)
    assert re.search(r"end: '2026-11-28'", block)
    assert re.search(r"reopen: '2026-11-30'", block)
    assert "9 AM–12 PM" in block
    assert set(_special_hours()) == {"2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28"}
