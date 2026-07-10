"""Unit tests for the PII-free lead-freshness detector in
scripts/sync_leads_to_bigquery.py (the 'no leads in N days' early warning)."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sync_leads_to_bigquery",
    Path(__file__).resolve().parent.parent / "scripts" / "sync_leads_to_bigquery.py",
)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def _iso(days_ago):
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_recent_lead_does_not_alert():
    rows = [{"created_at": _iso(0)}, {"created_at": _iso(1)}, {"created_at": _iso(10)}]
    f = sync.check_lead_freshness(rows, window_days=3)
    assert f["alert"] is False
    assert f["leads_recent"] == 2  # 0d + 1d within the 3d window
    assert f["leads_7d"] == 2
    assert f["days_since_last_lead"] == 0


def test_no_recent_lead_alerts():
    rows = [{"created_at": _iso(5)}, {"created_at": _iso(30)}]
    f = sync.check_lead_freshness(rows, window_days=3)
    assert f["alert"] is True
    assert f["leads_recent"] == 0
    assert f["leads_7d"] == 1  # only the 5d-ago lead falls in the 7d window


def test_empty_rows_alert_without_crashing():
    f = sync.check_lead_freshness([], window_days=3)
    assert f["alert"] is True
    assert f["last_lead_at"] is None
    assert f["days_since_last_lead"] is None


def test_naive_timestamp_parsed_as_utc():
    naive = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    f = sync.check_lead_freshness([{"created_at": naive}], window_days=3)
    assert f["leads_recent"] == 1


def test_garbage_timestamp_ignored_not_raised():
    f = sync.check_lead_freshness([{"created_at": "not-a-date"}, {"created_at": None}], window_days=3)
    assert f["alert"] is True
    assert f["last_lead_at"] is None
