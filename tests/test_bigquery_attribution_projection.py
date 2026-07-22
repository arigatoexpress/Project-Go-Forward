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
