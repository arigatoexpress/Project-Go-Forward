import json

from scripts.sync_leads_to_bigquery import VIEWS, project_event, project_lead

JOURNEY_ID = "j_0123456789abcdef0123456789abcdef"
FORBIDDEN = {
    "name",
    "phone",
    "email",
    "message",
    "notes",
    "client_ip",
    "journey_id",
    "session_id",
    "user_id",
}


def test_lead_and_event_share_only_a_hashed_journey_key():
    lead = project_lead(
        {
            "lead_id": "lead-1",
            "journey_id": JOURNEY_ID,
            "home_id": "home-42",
            "home_model": "Sapphire 3-Bed",
            "name": "Buyer",
            "phone": "2813243020",
            "email": "buyer@example.com",
        }
    )
    event = project_event(
        {
            "event": "lead_captured",
            "journey_id": JOURNEY_ID,
            "client_ip": "203.0.113.10",
            "props": {
                "home_id": "home-42",
                "home": "Sapphire 3-Bed",
                "email": "buyer@example.com",
                "message": "private",
            },
        }
    )

    assert lead["journey_key"] == event["journey_key"]
    assert len(lead["journey_key"]) == 64
    assert JOURNEY_ID not in json.dumps([lead, event])
    assert not FORBIDDEN.intersection(lead)
    assert not FORBIDDEN.intersection(event)
    assert "buyer@example.com" not in json.dumps(event)


def test_unknown_or_malformed_event_is_not_projected():
    assert project_event({"event": "scanner_noise", "journey_id": JOURNEY_ID}) is None
    assert project_event({"event": "lead_captured", "journey_id": "buyer@example.com"})[
        "journey_key"
    ] is None


def test_conversion_views_use_distinct_journeys_and_safe_rates():
    home_sql = VIEWS["v_home_conversion"]
    funnel_sql = VIEWS["v_journey_funnel"]
    assert "COUNT(DISTINCT" in home_sql
    assert "SAFE_DIVIDE" in home_sql
    assert "lead_captured" in home_sql and "appointment_booked" in home_sql
    assert "GROUP BY journey_key" in funnel_sql


def test_lead_response_projection_is_measurable_and_pii_free():
    projected = project_lead(
        {
            "lead_id": "lead-1",
            "created_at": "2026-07-22T12:00:00+00:00",
            "first_contacted_at": "2026-07-22T12:12:30+00:00",
            "first_contacted_by": "admin:private@example.com",
            "status_changed_at": "2026-07-22T12:12:30+00:00",
            "status_changed_by": "admin:private@example.com",
        }
    )

    assert projected["first_contacted_at"] == "2026-07-22T12:12:30+00:00"
    assert projected["status_changed_at"] == "2026-07-22T12:12:30+00:00"
    assert projected["has_first_contact"] is True
    assert projected["response_seconds"] == 750
    assert projected["response_within_15m"] is True
    assert "first_contacted_by" not in projected
    assert "status_changed_by" not in projected
    assert "private@example.com" not in json.dumps(projected)

    sla_sql = VIEWS["v_lead_response_sla"]
    assert "response_seconds" in sla_sql
    assert "APPROX_QUANTILES" in sla_sql
    assert "response_within_15m" in sla_sql
    assert "SAFE_DIVIDE" in sla_sql
