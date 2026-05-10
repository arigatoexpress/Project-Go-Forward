import json
from datetime import date

import pandas as pd

from tools.house_orders_sanitizer import (
    _detect_header_index,
    apply_firestore_patch_plan,
    build_firestore_patch_plan,
    build_sanitized_report,
    parse_house_orders_dataframe,
)


def test_parse_house_orders_keeps_only_safe_inventory_fields():
    df = pd.DataFrame(
        [
            {
                "Serial #": "SN-001",
                "Serial # 2": "SN-002",
                "HUD Label": "HUD-1",
                "HUD Label 2": "HUD-2",
                "Model": "The Nassau",
                "Manufacturer": "Jessup Housing",
                "Customer Name": "Jane Buyer",
                "Phone": "555-111-2222",
                "Credit Notes": "private credit note",
                "Invoice Amount": "$88,500",
                "MFG Date": date(2026, 1, 15),
                "Wind Zone": "II",
                "Section 1 Weight": "12,500",
                "Factory Address": "123 Factory Road",
                "Size": "28 x 56",
            }
        ]
    )

    rows, metadata = parse_house_orders_dataframe(df)

    assert metadata["source_rows"] == 1
    assert metadata["sanitized_rows"] == 1
    assert set(metadata["sensitive_columns_dropped"]) == {
        "Credit Notes",
        "Customer Name",
        "Phone",
    }
    assert metadata["safe_columns"]["serial_number"] == "Serial #"

    data = rows[0].data
    assert data == {
        "date_of_manufacture": "2026-01-15",
        "invoice_amount": 88500.0,
        "label_number": "HUD-1",
        "label_number_2": "HUD-2",
        "length": 56,
        "manufacturer": "Jessup Housing",
        "manufacturer_address": "123 Factory Road",
        "model_name": "The Nassau",
        "serial_number": "SN-001",
        "serial_number_2": "SN-002",
        "weight_sec_1": 12500,
        "width": 28,
        "wind_zone": "II",
    }

    report_text = json.dumps(build_sanitized_report(rows, metadata))
    assert "Jane Buyer" not in report_text
    assert "555-111-2222" not in report_text
    assert "private credit note" not in report_text


def test_detect_header_index_skips_title_preamble_rows():
    raw = pd.DataFrame(
        [
            ["Current Inventory", None, None, None],
            ["Serial #", "Model", "Customer", "Invoice Amount"],
            ["SN-001", "The Nassau", "Jane Buyer", "$88,500"],
        ]
    )

    assert _detect_header_index(raw) == 1


def test_row_without_primary_match_warns_and_apply_plan_skips():
    df = pd.DataFrame([{"Model": "The Cottage", "Manufacturer": "Cavco", "HUD Label": "HUD-7"}])

    rows, _metadata = parse_house_orders_dataframe(df)

    assert rows[0].warnings
    assert rows[0].stable_identifiers == {
        "model_name": "The Cottage",
    }

    db = FakeInventoryDB(existing_by_serial={})
    plans = build_firestore_patch_plan(rows, db)

    assert plans[0].match_status == "missing_match"
    assert plans[0].patch == {}
    assert apply_firestore_patch_plan(plans, db) == {"updated": 0, "skipped": 1, "failed": 0}
    assert db.updated == []


def test_patch_plan_matches_existing_inventory_and_only_updates_changed_safe_fields():
    df = pd.DataFrame(
        [
            {
                "Serial #": "SN-001",
                "Model": "The Nassau",
                "HUD Label": "HUD-1",
                "HUD Label 2": "HUD-2",
                "Customer Name": "Jane Buyer",
                "Salesman": "Should stay private",
            }
        ]
    )
    rows, _metadata = parse_house_orders_dataframe(df)
    db = FakeInventoryDB(
        existing_by_serial={
            "SN-001": {
                "id": "firestore-doc-1",
                "serial_number": "SN-001",
                "model_name": "The Nassau",
                "label_number": "OLD-HUD",
            }
        }
    )

    plans = build_firestore_patch_plan(rows, db)

    assert len(plans) == 1
    assert plans[0].match_status == "matched"
    assert plans[0].inventory_id == "firestore-doc-1"
    assert plans[0].patch == {
        "label_number": "HUD-1",
        "label_number_2": "HUD-2",
    }

    assert apply_firestore_patch_plan(plans, db) == {"updated": 1, "skipped": 0, "failed": 0}
    assert db.updated == [("firestore-doc-1", {"label_number": "HUD-1", "label_number_2": "HUD-2"})]


def test_patch_plan_can_opt_into_unique_model_match_when_serial_missing_from_firestore():
    df = pd.DataFrame(
        [
            {
                "Serial #": "SN-001",
                "Model": "Nassau",
                "HUD Label": "HUD-1",
            }
        ]
    )
    rows, _metadata = parse_house_orders_dataframe(df)
    db = FakeInventoryDB(
        existing_by_serial={},
        inventory=[
            {
                "id": "the-nassau",
                "model_name": "The Nassau",
                "serial_number": "",
                "label_number": "",
            }
        ],
    )

    without_model = build_firestore_patch_plan(rows, db)
    with_model = build_firestore_patch_plan(rows, db, allow_model_match=True)

    assert without_model[0].match_status == "missing_match"
    assert with_model[0].match_status == "matched"
    assert with_model[0].match_field == "model_name"
    assert with_model[0].inventory_id == "the-nassau"
    assert with_model[0].patch == {"label_number": "HUD-1", "serial_number": "SN-001"}


class FakeInventoryDB:
    def __init__(self, existing_by_serial=None, existing_by_id=None, inventory=None):
        self.existing_by_serial = existing_by_serial or {}
        self.existing_by_id = existing_by_id or {}
        self.inventory = inventory or []
        self.updated = []

    def get_inventory_by_id(self, inventory_id):
        return self.existing_by_id.get(inventory_id)

    def get_inventory_by_serial(self, serial_number):
        return self.existing_by_serial.get(serial_number)

    def search_inventory(self, status=None, limit=5000):
        return self.inventory[:limit]

    def update_inventory(self, inventory_id, patch):
        self.updated.append((inventory_id, patch))
