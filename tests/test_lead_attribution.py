"""Lead-source attribution categorization tests.

``main._categorize_lead_source`` maps a Lead-like object onto the coarse
buckets the CRM attribution chart consumes. Its precedence is:

    utm_source  >  referrer  >  raw source bucket

The real ``Lead`` dataclass now has ``utm_source`` / ``referrer`` fields
(PR 1), so the function reads them directly when present. We exercise those
branches with a lightweight ``SimpleNamespace`` fake (no Firestore), and
cover the raw-source fallbacks with the actual ``Lead`` dataclass.

``main`` instantiates a Firestore client at import (``lead_manager =
LeadManager(...)``), which needs GCP credentials the CI "no Firestore/GCS"
job lacks. So ``_categorize_lead_source`` is pulled in via the ``categorize``
fixture, which first calls ``create_client`` to stub those eager imports the
same way the rest of the suite does — importing ``main`` raw at module top
errors during collection in a creds-less environment.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lead_management import Lead

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def categorize(monkeypatch):
    """Return ``main._categorize_lead_source`` with Firestore stubbed out."""
    from test_api_v1 import create_client

    create_client(monkeypatch)
    from main import _categorize_lead_source

    return _categorize_lead_source


def _fake_lead(source=None, utm_source=None, referrer=None):
    """A minimal Lead-like object — no Firestore, no dataclass overhead."""
    return SimpleNamespace(source=source, utm_source=utm_source, referrer=referrer)


def test_utm_source_takes_priority_and_is_lowercased(categorize):
    # utm_source="Instagram" -> "utm:instagram"; utm wins even when a raw
    # source and referrer are also present.
    lead = _fake_lead(source="chat", utm_source="Instagram", referrer="https://t.co/x")
    assert categorize(lead) == "utm:instagram"


def test_referrer_falls_back_to_host(categorize):
    # No utm -> referrer host (protocol + path stripped).
    lead = _fake_lead(referrer="https://www.google.com/search?q=mobile+homes")
    assert categorize(lead) == "referrer:www.google.com"


def test_referrer_host_is_truncated_to_40_chars(categorize):
    host = "a" * 60
    lead = _fake_lead(referrer=f"http://{host}.com/path")
    result = categorize(lead)
    assert result.startswith("referrer:")
    assert result == "referrer:" + ("a" * 40)


def test_referrer_with_no_host_becomes_direct(categorize):
    lead = _fake_lead(referrer="https://")
    assert categorize(lead) == "referrer:direct"


def test_known_raw_source_bucket(categorize):
    # No utm / referrer -> the raw source bucket. "chat" passes through.
    lead = _fake_lead(source="chat")
    assert categorize(lead) == "chat"


def test_chat_intake_normalized_to_chat(categorize):
    lead = _fake_lead(source="chat_intake")
    assert categorize(lead) == "chat"


def test_empty_source_is_other(categorize):
    lead = _fake_lead(source="")
    assert categorize(lead) == "other"


def test_unknown_raw_source_passes_through_lowercased(categorize):
    lead = _fake_lead(source="Facebook_Ad")
    assert categorize(lead) == "facebook_ad"


def test_real_lead_dataclass_uses_source_bucket(categorize):
    # A genuine Lead without utm_source/referrer set; getattr defaults keep
    # the function on the raw-source branch without raising AttributeError.
    lead = Lead(lead_id="L1", user_id="U1", session_id="S1", source="instagram")
    assert categorize(lead) == "instagram"


def test_lead_sources_buckets_utm_instagram(categorize):
    # A real Lead with utm_source="instagram" (PR 1 field) flows through to
    # the utm:instagram bucket, closing the loop between the real field and
    # the existing categorization reader.
    lead = Lead(
        lead_id="L2",
        user_id="U2",
        session_id="S2",
        source="website",
        utm_source="instagram",
    )
    assert categorize(lead) == "utm:instagram"
