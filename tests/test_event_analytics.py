"""GET /api/analytics/events — surfaces the client event stream that
POST /api/analytics captures into analytics_events (home views, quote/tour
clicks, form opens). Previously captured and never read ("flying blind").
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def _seed_events(main, events):
    main._db.db.collections["analytics_events"] = {
        f"e{i}": e for i, e in enumerate(events)
    }


def test_event_analytics_requires_admin(monkeypatch):
    client, *_ = create_client(monkeypatch)
    assert client.get("/api/analytics/events").status_code in (401, 403)


def test_event_analytics_aggregates_real_events(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    token = main._create_admin_token()
    now = datetime.now(UTC).isoformat()
    _seed_events(
        main,
        [
            {"event": "lead_captured", "props": {"home": "Nassau"}, "created_at": now},
            {"event": "lead_captured", "props": {"home": "Nassau"}, "created_at": now},
            {"event": "tour_click", "props": {"home": "Premier"}, "created_at": now},
        ],
    )
    body = client.get(
        "/api/analytics/events?range=30d", headers={"X-Admin-Token": token}
    ).json()
    assert body["total_events"] == 3
    assert {"event": "lead_captured", "count": 2} in body["by_type"]
    assert body["top_homes"][0] == {"home": "Nassau", "count": 2}
    assert body["daily_trend"] and body["daily_trend"][0]["events"] == 3


def test_event_analytics_respects_range_window(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    token = main._create_admin_token()
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    recent = datetime.now(UTC).isoformat()
    _seed_events(
        main,
        [
            {"event": "home_view", "props": {"home": "Old"}, "created_at": old},
            {"event": "home_view", "props": {"home": "Recent"}, "created_at": recent},
        ],
    )
    # 30-day window excludes the 60-day-old event.
    body = client.get(
        "/api/analytics/events?range=30d", headers={"X-Admin-Token": token}
    ).json()
    assert body["total_events"] == 1
    assert body["top_homes"][0]["home"] == "Recent"


def test_event_analytics_all_range_includes_old_events(monkeypatch):
    """range=all (the 'All Time' tab) must NOT silently fall back to 30 days."""
    client, main, *_ = create_client(monkeypatch)
    token = main._create_admin_token()
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    recent = datetime.now(UTC).isoformat()
    _seed_events(
        main,
        [
            {"event": "home_view", "props": {"home": "Ancient"}, "created_at": old},
            {"event": "home_view", "props": {"home": "Recent"}, "created_at": recent},
        ],
    )
    body = client.get(
        "/api/analytics/events?range=all", headers={"X-Admin-Token": token}
    ).json()
    assert body["total_events"] == 2  # both, including the 400-day-old event
    assert {h["home"] for h in body["top_homes"]} == {"Ancient", "Recent"}


def test_event_analytics_never_500s_on_empty(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    token = main._create_admin_token()
    body = client.get(
        "/api/analytics/events", headers={"X-Admin-Token": token}
    ).json()
    assert body["total_events"] == 0 and body["by_type"] == []


def test_event_analytics_reports_anonymous_journey_funnel(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    token = main._create_admin_token()
    now = datetime.now(UTC).isoformat()
    first = "j_0123456789abcdef0123456789abcdef"
    second = "j_fedcba9876543210fedcba9876543210"
    _seed_events(
        main,
        [
            {"event": "page_viewed", "journey_id": first, "props": {}, "created_at": now},
            {"event": "lead_form_opened", "journey_id": first, "props": {}, "created_at": now},
            {"event": "lead_captured", "journey_id": first, "props": {}, "created_at": now},
            {"event": "appointment_booked", "journey_id": first, "props": {}, "created_at": now},
            {"event": "page_viewed", "journey_id": second, "props": {}, "created_at": now},
            {"event": "phone_clicked", "journey_id": second, "props": {}, "created_at": now},
            {"event": "scanner_noise", "journey_id": second, "props": {}, "created_at": now},
        ],
    )

    body = client.get(
        "/api/analytics/events?range=30d", headers={"X-Admin-Token": token}
    ).json()

    assert body["total_events"] == 6
    assert body["discarded_events"] == 1
    assert body["journey_funnel"] == {
        "eligible_journeys": 2,
        "lead_form_journeys": 1,
        "lead_journeys": 1,
        "appointment_journeys": 1,
        "phone_journeys": 1,
        "lead_conversion_rate": 50.0,
        "appointment_conversion_rate": 50.0,
        "attribution_coverage_pct": 100.0,
    }
