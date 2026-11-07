"""Customer chat events stay on the journey funnel and out of the message body."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

JOURNEY_ID = "j_0123456789abcdef0123456789abcdef"
MESSAGE = "Please call 2813243020 or email buyer@example.com about the Nassau."


def _events(main):
    return list(main._db.db.collections.get("analytics_events", {}).values())


def _capture_info(monkeypatch, main):
    logged = []

    def info(message, **kwargs):
        logged.append({"message": message, **kwargs})

    monkeypatch.setattr(main.struct_logger, "info", info)
    return logged


def test_chat_prompt_is_stored_logged_and_stripped_of_message_text(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    logged = _capture_info(monkeypatch, main)

    response = client.post(
        "/api/analytics",
        json={
            "event": "chat_prompt_shown",
            "page_path": "/",
            "path": "/",
            "placement": "mobile_prompt",
            "source": "mobile_prompt",
            "viewport": "phone",
            "journey_id": JOURNEY_ID,
            "message": MESSAGE,
            "text": MESSAGE,
            "email": "buyer@example.com",
            "phone": "2813243020",
            "message_length": "call me at 2813243020",
        },
    )

    assert response.json() == {"ok": True}
    event = next(item for item in _events(main) if item["event"] == "chat_prompt_shown")
    assert event["journey_id"] == JOURNEY_ID
    assert event["props"] == {
        "page_path": "/",
        "path": "/",
        "placement": "mobile_prompt",
        "source": "mobile_prompt",
        "viewport": "phone",
    }
    blob = json.dumps(event)
    assert "buyer@example.com" not in blob
    assert "2813243020" not in blob
    assert "Nassau" not in blob

    chat_logs = [row for row in logged if row.get("event") == "storefront.chat.prompt_shown"]
    assert len(chat_logs) == 1
    assert chat_logs[0]["lane"] == "pgf"
    assert chat_logs[0]["service"] == "pgf"
    assert chat_logs[0]["journey_id"] == JOURNEY_ID
    assert chat_logs[0]["viewport"] == "phone"
    log_blob = json.dumps(chat_logs[0])
    assert "buyer@example.com" not in log_blob
    assert "2813243020" not in log_blob
    assert "message_length" not in chat_logs[0]


def test_chat_message_sent_keeps_length_only(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    logged = _capture_info(monkeypatch, main)

    client.post(
        "/api/analytics",
        json={
            "event": "chat_message_sent",
            "kind": "typed",
            "source": "typed",
            "viewport": "phone",
            "page_path": "/chat",
            "message_length": 42,
            "message": MESSAGE,
            "journey_id": JOURNEY_ID,
        },
    )

    event = next(item for item in _events(main) if item["event"] == "chat_message_sent")
    assert event["props"]["message_length"] == "42"
    assert event["props"]["kind"] == "typed"
    assert "message" not in event["props"]
    assert MESSAGE not in json.dumps(event)

    chat_logs = [row for row in logged if row.get("event") == "storefront.chat.message_sent"]
    assert chat_logs[0]["message_length"] == 42
    assert chat_logs[0]["lane"] == "pgf"
    assert MESSAGE not in json.dumps(chat_logs[0])


def test_unknown_chat_text_event_is_not_stored(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    logged = _capture_info(monkeypatch, main)
    response = client.post(
        "/api/analytics",
        json={"event": "chat_transcript", "message": MESSAGE, "journey_id": JOURNEY_ID},
    )
    assert response.json() == {"ok": True}
    assert _events(main) == []
    assert [row for row in logged if row.get("lane") == "pgf"] == []


def test_event_analytics_counts_chat_journeys(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    token = main._create_admin_token()
    now = datetime.now(UTC).isoformat()
    main._db.db.collections["analytics_events"] = {
        "prompt": {
            "event": "chat_prompt_shown",
            "journey_id": JOURNEY_ID,
            "props": {},
            "created_at": now,
        },
        "opened": {
            "event": "chat_opened",
            "journey_id": JOURNEY_ID,
            "props": {},
            "created_at": now,
        },
        "started": {
            "event": "chat_started",
            "journey_id": JOURNEY_ID,
            "props": {},
            "created_at": now,
        },
        "other": {
            "event": "page_viewed",
            "journey_id": "j_fedcba9876543210fedcba9876543210",
            "props": {},
            "created_at": now,
        },
    }

    body = client.get("/api/analytics/events?range=30d", headers={"X-Admin-Token": token}).json()
    funnel = body["journey_funnel"]
    assert funnel["eligible_journeys"] == 2
    assert funnel["chat_prompt_journeys"] == 1
    assert funnel["chat_opened_journeys"] == 1
    assert funnel["chat_started_journeys"] == 1
    assert {"event": "chat_started", "count": 1} in body["by_type"]
