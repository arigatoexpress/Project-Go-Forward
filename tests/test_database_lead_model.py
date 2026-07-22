import pytest
from pydantic import ValidationError

from database.models import LeadRecord


def test_lead_record_roundtrips_google_click_ids():
    record = LeadRecord(
        lead_id="contact_123_abcd",
        user_id="contact_form",
        session_id="contact_123",
        gclid="EAIaIQobChMI_valid-123",
        gbraid="GBRAID_valid_456",
        wbraid="WBRAID_valid_789",
    )
    body = record.model_dump()
    assert body["gclid"] == "EAIaIQobChMI_valid-123"
    assert body["gbraid"] == "GBRAID_valid_456"
    assert body["wbraid"] == "WBRAID_valid_789"


def test_lead_record_rejects_malformed_click_ids():
    with pytest.raises(ValidationError):
        LeadRecord(
            lead_id="contact_123_abcd",
            user_id="contact_form",
            session_id="contact_123",
            gclid="<script>alert(1)</script>",
        )


def test_lead_record_validates_anonymous_journey_attribution():
    record = LeadRecord(
        lead_id="contact_123_abcd",
        user_id="contact_form",
        session_id="contact_123",
        journey_id="j_0123456789abcdef0123456789abcdef",
        home_id="home-42",
        home_model="Sapphire 3-Bed",
    )
    assert record.model_dump()["journey_id"] == "j_0123456789abcdef0123456789abcdef"

    with pytest.raises(ValidationError):
        LeadRecord(
            lead_id="contact_123_abcd",
            user_id="contact_form",
            session_id="contact_123",
            journey_id="buyer@example.com",
        )
