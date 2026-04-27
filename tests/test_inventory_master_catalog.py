"""
Unit tests for tools/inventory_master_catalog.py.

Covers:
- Reject of House Orders.xlsx-style PII columns (validate_columns).
- Clean-catalog parse → Pydantic Inventory validation.
- Size column variant ("32x60") parsing.
- Manufacturer alias normalization.
- Currency-string MSRP coercion ("$45,900" → 45900.0).
"""

import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.inventory_master_catalog import (
    CatalogRow,
    _clean_text,
    _parse_size,
    _resolve_manufacturer,
    _to_float,
    parse_rows,
    validate_columns,
    write_to_firestore,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestValidateColumns:
    def test_rejects_house_orders_style(self):
        df = _df([{"Customer": "x", "Customer #": 1, "Salesman": "y", "Manufacturer": "Cavco"}])
        with pytest.raises(ValueError, match="forbidden PII"):
            validate_columns(df)

    def test_rejects_credit_score_column(self):
        df = _df([{"Manufacturer": "Cavco", "Credit Score": 720, "Model": "X", "Serial": "1"}])
        with pytest.raises(ValueError):
            validate_columns(df)

    def test_accepts_clean_catalog(self):
        df = _df(
            [
                {
                    "Manufacturer": "Cavco",
                    "Model": "X",
                    "Serial": "1",
                    "MSRP": 1,
                    "Width": 16,
                    "Length": 76,
                }
            ]
        )
        validate_columns(df)  # no raise

    def test_warns_on_unexpected_extra_column(self, caplog):
        df = _df([{"Manufacturer": "Cavco", "Model": "X", "Serial": "1", "Mystery": "?"}])
        validate_columns(df)
        assert any("unexpected columns" in r.message for r in caplog.records)


class TestParseRows:
    def test_clean_catalog_yields_valid_inventory(self):
        df = _df(
            [
                {
                    "Manufacturer": "Cavco",
                    "Model": "Mountain Delight",
                    "Serial #": "CAV-001",
                    "Width": 16,
                    "Length": 76,
                    "MSRP": "$45,900",
                    "Bedrooms": 3,
                    "Bathrooms": 2,
                    "SqFt": 1216,
                },
            ]
        )
        rows = parse_rows(df)
        assert len(rows) == 1
        inv = rows[0].to_inventory()
        assert inv.serial_number == "CAV-001"
        assert inv.manufacturer == "cavco"
        assert inv.msrp == 45900.0
        assert inv.width == 16 and inv.length == 76

    def test_size_column_variant(self):
        df = _df(
            [
                {
                    "Manufacturer": "New Vision",
                    "Model": "The Big Steve",
                    "Plan ID": "225053",
                    "Size": "32x56",
                    "MSRP": 125000,
                }
            ]
        )
        rows = parse_rows(df)
        assert rows[0].width == 32 and rows[0].length == 56

    def test_numeric_serial_from_sparse_excel_column_drops_decimal_suffix(self):
        df = _df(
            [
                {"Manufacturer": "New Vision", "Model": "The Big Steve", "Plan ID": 225053.0},
                {"Manufacturer": "New Vision", "Model": "Missing Serial", "Plan ID": None},
            ]
        )
        rows = parse_rows(df)
        assert len(rows) == 1
        assert rows[0].serial_number == "225053"

    def test_skips_row_without_serial(self):
        df = _df(
            [
                {"Manufacturer": "Cavco", "Model": "X", "Serial": "1", "MSRP": 1},
                {"Manufacturer": "Cavco", "Model": "Y", "Serial": None, "MSRP": 1},
            ]
        )
        rows = parse_rows(df)
        assert len(rows) == 1
        assert rows[0].serial_number == "1"

    def test_unknown_manufacturer_raises(self):
        df = _df([{"Manufacturer": "Bigfoot Homes", "Model": "X", "Serial": "1"}])
        with pytest.raises(ValueError, match="Unknown manufacturer"):
            parse_rows(df)

    def test_empty_model_raises(self):
        df = _df([{"Manufacturer": "Cavco", "Model": "", "Serial": "1"}])
        with pytest.raises(ValueError, match="model is empty"):
            parse_rows(df)


class TestNormalizationHelpers:
    def test_parse_size_valid(self):
        assert _parse_size("32x60") == (32, 60)
        assert _parse_size("28X66") == (28, 66)
        assert _parse_size("16 x 76") == (16, 76)

    def test_parse_size_invalid(self):
        assert _parse_size("not a size") == (None, None)
        assert _parse_size(None) == (None, None)
        assert _parse_size(float("nan")) == (None, None)

    def test_resolve_manufacturer_aliases(self):
        assert _resolve_manufacturer("Tru Belton") == "trumh"
        assert _resolve_manufacturer("CHAMPION LOUISIANA") == "champion_la"
        assert _resolve_manufacturer("  Skyline   Kansas  ") == "skyline_ks"

    def test_to_float_coerces_currency_strings(self):
        assert _to_float("$45,900") == 45900.0
        assert _to_float("99,500.50") == 99500.50
        assert _to_float(None) is None
        assert _to_float("not a number") is None

    def test_clean_text_keeps_string_ids_but_normalizes_integral_floats(self):
        assert _clean_text("00123") == "00123"
        assert _clean_text(225053.0) == "225053"


class TestWriteToFirestore:
    def test_upsert_uses_doc_id_without_writing_model_id(self, monkeypatch):
        row = CatalogRow(
            serial_number="CAV-001",
            manufacturer_key="cavco",
            model_name="Mountain Delight",
            width=16,
            length=76,
            msrp=45900.0,
            bedrooms=3,
            bathrooms=2,
            sqft=1216,
            status="AVAILABLE",
            notes=None,
        )

        class FakeDB:
            def __init__(self):
                self.updated = []

            def get_inventory_by_serial(self, serial_number):
                assert serial_number == "CAV-001"
                return {"id": "firestore-doc-id"}

            def update_inventory(self, inventory_id, data):
                self.updated.append((inventory_id, data))

        db = FakeDB()
        fake_firestore_client = types.SimpleNamespace(THODatabase=lambda: db)
        monkeypatch.setitem(sys.modules, "database.firestore_client", fake_firestore_client)

        counts = write_to_firestore([row], dry_run=False)

        assert counts == {"created": 0, "updated": 1, "failed": 0}
        assert db.updated[0][0] == "firestore-doc-id"
        assert "id" not in db.updated[0][1]
