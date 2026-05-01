"""Unit tests for the `/api/deals` home-info enrichment helper.

Covers `_enrich_deals_with_home` in `main.py`, which joins deal records
against the `inventory` collection by serial number so the CRM Pipeline
kanban can render the home's model + manufacturer instead of the empty
"No home selected" placeholder.

Run: python -m pytest tests/test_deal_home_enrichment.py -v
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def main_module(monkeypatch):
    """Import `main` with all Firestore-touching dependencies stubbed.

    Mirrors the dance from test_deal_document_validation.py so we can
    exercise the pure helper without GCP credentials.
    """
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = frontend_dist / "index.html"
    if not index_html.exists():
        index_html.write_text("<html><body>test</body></html>")

    class _FakeFirestoreClient:
        def __init__(self, *args, **kwargs):
            pass

        def collection(self, *args, **kwargs):
            raise RuntimeError("Tests must not exercise live Firestore queries")

    from google.cloud import firestore as _firestore_module
    monkeypatch.setattr(_firestore_module, "Client", _FakeFirestoreClient)

    fake_db = types.SimpleNamespace(
        get_deal=lambda deal_id: None,
        get_inventory_by_serials=lambda serials: {},
        get_inventory_by_serial=lambda serial: None,
    )

    fake_firestore_client_module = types.ModuleType("database.firestore_client")
    fake_firestore_client_module.get_database = lambda: fake_db
    fake_firestore_client_module.THODatabase = type("THODatabase", (), {})
    monkeypatch.setitem(
        sys.modules, "database.firestore_client", fake_firestore_client_module
    )

    sys.modules.pop("main", None)
    return importlib.import_module("main")


class _FakeDB:
    """Captures inputs to assert batching behaviour."""

    def __init__(self, inventory_by_serial: dict):
        self._inv = inventory_by_serial
        self.calls: list[list[str]] = []

    def get_inventory_by_serials(self, serials):
        self.calls.append(list(serials))
        return {s: self._inv[s] for s in serials if s in self._inv}


class TestEnrichDealsWithHome:
    """Pure unit tests for the helper."""

    def test_serial_only_deal_gets_inventory_join(self, main_module):
        deal = {
            "id": "deal-1",
            "buyer_first_name": "Garett",
            "buyer_last_name": "Floyd",
            "serial_number": "1394",
            "model": None,
            "manufacturer": None,
        }
        db = _FakeDB({
            "1394": {
                "model_name": "Champion 38SLT",
                "manufacturer": "Champion Homes",
                "serial_number": "1394",
            }
        })
        result = main_module._enrich_deals_with_home([deal], db)
        assert result[0]["home_label"] == "Champion 38SLT"
        assert result[0]["home_manufacturer"] == "Champion Homes"
        # Original keys preserved
        assert result[0]["serial_number"] == "1394"
        assert result[0]["buyer_first_name"] == "Garett"

    def test_serial_with_no_inventory_match_falls_back_to_no_label(self, main_module):
        deal = {
            "id": "deal-2",
            "serial_number": "8407",
            "model": None,
        }
        db = _FakeDB({})  # no inventory match
        result = main_module._enrich_deals_with_home([deal], db)
        # No home_label set -> frontend will render "Serial: 8407" fallback
        assert "home_label" not in result[0]
        assert result[0]["serial_number"] == "8407"

    def test_deal_with_existing_model_does_not_query_inventory(self, main_module):
        """Deals already carrying manufacturer + model should skip the join."""
        deal = {
            "id": "deal-3",
            "manufacturer": "Clayton",
            "model": "TruMH 1680",
            "serial_number_1": "SN-9999",
        }
        db = _FakeDB({})
        result = main_module._enrich_deals_with_home([deal], db)
        # Existing fields untouched + home_label normalized for the frontend
        assert result[0]["model"] == "TruMH 1680"
        assert result[0]["home_label"] == "TruMH 1680"
        assert result[0]["home_manufacturer"] == "Clayton"
        # No batched lookup happened
        assert db.calls == []

    def test_serial_number_1_alias_is_honored(self, main_module):
        """import_fcd_deals.py writes `serial_number_1`; the legacy bulk
        import wrote `serial_number`. The helper must accept both."""
        deal = {
            "id": "deal-4",
            "serial_number_1": "ABC123",
            "model": None,
        }
        db = _FakeDB({
            "ABC123": {
                "model_name": "Test Home",
                "manufacturer": "Acme",
                "serial_number": "ABC123",
            }
        })
        result = main_module._enrich_deals_with_home([deal], db)
        assert result[0]["home_label"] == "Test Home"
        assert result[0]["home_manufacturer"] == "Acme"

    def test_lookup_is_batched_not_per_deal(self, main_module):
        """All serials must be sent in a single (or chunked) call, not N
        per-deal calls."""
        deals = [
            {"id": f"deal-{i}", "serial_number": f"S{i}", "model": None}
            for i in range(5)
        ]
        db = _FakeDB({
            "S0": {"model_name": "Home0", "manufacturer": "M", "serial_number": "S0"},
            "S2": {"model_name": "Home2", "manufacturer": "M", "serial_number": "S2"},
        })
        main_module._enrich_deals_with_home(deals, db)
        # Single batched call, all 5 serials, regardless of which had matches
        assert len(db.calls) == 1
        assert sorted(db.calls[0]) == ["S0", "S1", "S2", "S3", "S4"]

    def test_empty_input_is_safe(self, main_module):
        db = _FakeDB({})
        assert main_module._enrich_deals_with_home([], db) == []

    def test_deal_with_no_serial_or_model_passes_through(self, main_module):
        deal = {"id": "empty-deal"}
        db = _FakeDB({})
        result = main_module._enrich_deals_with_home([deal], db)
        assert result[0] == {"id": "empty-deal"}
        # No lookup attempted
        assert db.calls == []

    def test_inventory_lookup_failure_does_not_break_listing(self, main_module):
        """If the batched lookup raises, deals must still be returned —
        enrichment is best-effort."""
        class _BrokenDB:
            def get_inventory_by_serials(self, serials):
                raise RuntimeError("Firestore is on fire")

        deal = {"id": "deal-5", "serial_number": "8407"}
        result = main_module._enrich_deals_with_home([deal], _BrokenDB())
        # Original deal still returned; just no home_label
        assert result[0]["id"] == "deal-5"
        assert result[0]["serial_number"] == "8407"
        assert "home_label" not in result[0]

    def test_falls_back_to_per_serial_lookup_if_batch_unavailable(self, main_module):
        """Older test stubs / DB shims may only implement the singular
        method. The helper must degrade gracefully."""
        class _LegacyDB:
            def __init__(self, inv):
                self._inv = inv
                self.calls = []

            def get_inventory_by_serial(self, serial):
                self.calls.append(serial)
                return self._inv.get(serial)

        deal = {"id": "deal-6", "serial_number": "9949", "model": None}
        db = _LegacyDB({
            "9949": {
                "model_name": "Elite Series Jackson",
                "manufacturer": "Jessup Housing",
                "serial_number": "9949",
            }
        })
        result = main_module._enrich_deals_with_home([deal], db)
        assert result[0]["home_label"] == "Elite Series Jackson"
        assert result[0]["home_manufacturer"] == "Jessup Housing"
        assert db.calls == ["9949"]

    def test_home_model_field_is_recognized(self, main_module):
        """The legacy bulk import wrote `home_model` (not `model`) on deal
        records. When that's already populated we should surface it as
        `home_label` without an inventory lookup."""
        deal = {
            "id": "deal-7",
            "home_model": "S-1272-32A",
            "manufacturer": "Champion",
            "serial_number": "8407",
        }
        db = _FakeDB({})
        result = main_module._enrich_deals_with_home([deal], db)
        assert result[0]["home_label"] == "S-1272-32A"
        assert result[0]["home_manufacturer"] == "Champion"
        # No lookup needed since manufacturer + home_model present
        assert db.calls == []
