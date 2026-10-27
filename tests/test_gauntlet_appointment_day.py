"""Appointment gauntlet day selection across a month that ends on Sunday."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_appointment_day_selector_crosses_a_month_ending_on_sunday():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    completed = subprocess.run(
        [node, "--test", "e2e/gauntlet/appointmentDay.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gauntlet_spec_uses_the_month_boundary_selector():
    text = (ROOT / "e2e/gauntlet/gauntlet.spec.js").read_text(encoding="utf-8")
    assert "clickEnabledAppointmentDay(page)" in text
    assert "from './appointmentDay.mjs'" in text
    assert "disabled: false" not in text
