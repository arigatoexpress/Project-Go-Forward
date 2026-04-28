"""
Inventory validation tests — Pydantic-only (no Firestore round-trip).

Covers each validator branch added in `database/models.py::Inventory`:
  * spec validators (bedrooms, bathrooms, sqft) accept None and >= 0
  * spec validators reject negative values via Pydantic's field-level ge=0
  * AVAILABLE inventory must have msrp or sale_price > 0
  * non-AVAILABLE statuses (SOLD, PENDING, RESERVED) skip the price check

These tests intentionally do not call `to_firestore_dict()` from
`tools/inventory_sync.py` — that path is exercised separately. Here we
target the model contract so future refactors can rely on it.
"""

import logging
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent))

from database.models import Inventory, InventoryStatus
from test_api_v1 import create_client
from tools.inventory_sync import InventoryItem

# ─────────────────────────────────────────────────────────────────────────────
# Spec validators
# ─────────────────────────────────────────────────────────────────────────────


def _base_kwargs(**overrides) -> dict:
    """Minimal kwargs that produce a valid AVAILABLE Inventory."""
    base = {
        "serial_number": "SN-001",
        "model_name": "The Steve",
        "msrp": 75000.0,
        "status": InventoryStatus.AVAILABLE,
    }
    base.update(overrides)
    return base


def test_specs_accept_none():
    """Specs are Optional — None must not trigger validation."""
    inv = Inventory(**_base_kwargs(bedrooms=None, bathrooms=None, sqft=None))
    assert inv.bedrooms is None
    assert inv.bathrooms is None
    assert inv.sqft is None


def test_specs_accept_zero_and_positive():
    """0 and positive ints are valid for the spec triple."""
    inv = Inventory(**_base_kwargs(bedrooms=0, bathrooms=2, sqft=1820))
    assert inv.bedrooms == 0
    assert inv.bathrooms == 2
    assert inv.sqft == 1820


@pytest.mark.parametrize("field", ["bedrooms", "bathrooms", "sqft"])
def test_specs_reject_negative(field):
    """Negative spec values must raise ValidationError."""
    kwargs = _base_kwargs(**{field: -1})
    with pytest.raises(ValidationError) as exc_info:
        Inventory(**kwargs)
    # Either Pydantic's ge=0 message or our custom message — both are acceptable
    # signals that negatives are blocked.
    msg = str(exc_info.value).lower()
    assert field in msg
    assert ("greater than or equal" in msg) or (">= 0" in msg)


# ─────────────────────────────────────────────────────────────────────────────
# AVAILABLE-status price requirement
# ─────────────────────────────────────────────────────────────────────────────


def test_available_with_msrp_only_passes():
    inv = Inventory(**_base_kwargs(msrp=100.0, sale_price=None))
    # use_enum_values=True yields the string value
    assert inv.status == InventoryStatus.AVAILABLE.value


def test_available_with_sale_price_only_passes():
    inv = Inventory(**_base_kwargs(msrp=None, sale_price=49999.99))
    assert inv.status == InventoryStatus.AVAILABLE.value


def test_available_with_both_prices_passes():
    inv = Inventory(**_base_kwargs(msrp=100.0, sale_price=90.0))
    assert inv.status == InventoryStatus.AVAILABLE.value


def test_available_without_any_price_raises():
    """AVAILABLE with no msrp and no sale_price is the canonical bad case."""
    kwargs = _base_kwargs(msrp=None, sale_price=None)
    with pytest.raises(ValidationError) as exc_info:
        Inventory(**kwargs)
    assert "AVAILABLE inventory must have msrp or sale_price > 0" in str(exc_info.value)


def test_available_with_zero_prices_raises():
    """0 is not a real price for an AVAILABLE home."""
    kwargs = _base_kwargs(msrp=0.0, sale_price=0.0)
    with pytest.raises(ValidationError) as exc_info:
        Inventory(**kwargs)
    assert "AVAILABLE inventory must have msrp or sale_price > 0" in str(exc_info.value)


@pytest.mark.parametrize(
    "status", [InventoryStatus.SOLD, InventoryStatus.PENDING, InventoryStatus.RESERVED]
)
def test_non_available_statuses_skip_price_check(status):
    """SOLD / PENDING / RESERVED don't need a current price (sale_price is on Sale)."""
    inv = Inventory(**_base_kwargs(msrp=None, sale_price=None, status=status))
    assert inv.status == status.value


# ─────────────────────────────────────────────────────────────────────────────
# inventory_sync soft-validation: warns but never raises
# ─────────────────────────────────────────────────────────────────────────────


def _inventory_item(**overrides) -> InventoryItem:
    """Build an InventoryItem with sensible defaults — overrides surface bad data."""
    base = dict(
        id="test-1",
        model_name="The Steve",
        manufacturer="TRU Homes",
        manufacturer_id="2007",
        plan_id="225053",
        year=2025,
        is_new=True,
        bedrooms=3,
        bathrooms=2,
        sqft=1820,
        width=28,
        length=66,
        msrp=125000.0,
        sale_price=119000.0,
        status="AVAILABLE",
        serial_number="SN-test-1",
        sections=2,
        condition="New",
        image_url=None,
        hero_image=None,
        photos=[],
        floorplan_url=None,
        matterport_id=None,
        tags=[],
        description=None,
        features=[],
        location="Huffman, TX",
        date_added="2026-04-27T00:00:00",
        source_url="https://example.com/x",
    )
    base.update(overrides)
    return InventoryItem(**base)


def test_to_firestore_dict_returns_dict_for_valid_item(caplog):
    """Happy path — no warnings, full dict returned."""
    item = _inventory_item()
    with caplog.at_level(logging.WARNING, logger="tools.inventory_sync"):
        out = item.to_firestore_dict()
    assert out["model_name"] == "The Steve"
    assert out["status"] == "AVAILABLE"
    # No validation warnings for a clean item.
    assert not any("Inventory validation failed" in r.message for r in caplog.records)


def test_to_firestore_dict_warns_but_returns_for_bad_item(caplog):
    """AVAILABLE with no price -> WARNING, but dict still returned (sync proceeds)."""
    item = _inventory_item(msrp=0.0, sale_price=0.0)
    with caplog.at_level(logging.WARNING, logger="tools.inventory_sync"):
        out = item.to_firestore_dict()
    # Sync must not be blocked.
    assert isinstance(out, dict)
    assert out["legacy_inventory_id"] == "test-1"
    # Warning fired with the violation reason.
    warnings = [r for r in caplog.records if "Inventory validation failed" in r.message]
    assert warnings, "expected a WARNING log for invalid AVAILABLE inventory"
    assert "test-1" in warnings[0].getMessage()


# ─────────────────────────────────────────────────────────────────────────────
# bulk-import endpoint hard-validation
# ─────────────────────────────────────────────────────────────────────────────


def test_bulk_import_rejects_invalid_available_inventory(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    token = main._create_admin_token()

    response = client.post(
        "/api/inventory/bulk-import",
        headers={"X-Admin-Token": token},
        json={
            "items": [
                {
                    "id": "bulk-bad",
                    "model_name": "No Price Home",
                    "serial_number": "SN-BAD",
                    "status": "AVAILABLE",
                    "msrp": 0,
                    "sale_price": 0,
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["imported"] == 0
    assert body["updated"] == 0
    assert body["errors"]
    assert "AVAILABLE inventory must have msrp or sale_price > 0" in body["errors"][0]
    assert "bulk-bad" not in fake_db.collections["inventory"]


def test_bulk_import_writes_valid_inventory_after_model_validation(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    token = main._create_admin_token()

    response = client.post(
        "/api/inventory/bulk-import",
        headers={"X-Admin-Token": token},
        json={
            "items": [
                {
                    "id": "bulk-good",
                    "model_name": "Priced Home",
                    "manufacturer": "Champion",
                    "serial_number": "SN-GOOD",
                    "status": "AVAILABLE",
                    "msrp": 75000,
                    "sale_price": 69900,
                    "bedrooms": 3,
                    "bathrooms": 2,
                    "sqft": 1200,
                }
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["imported"] == 1
    assert body["updated"] == 0
    stored = fake_db.collections["inventory"]["bulk-good"]
    assert stored["id"] == "bulk-good"
    assert stored["serial_number"] == "SN-GOOD"
    assert stored["sale_price"] == 69900
