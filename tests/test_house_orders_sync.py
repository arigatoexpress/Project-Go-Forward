"""Tests for the House Orders -> Firestore inventory sync.

The sync is the live-inventory unfreeze: it reflects the operator's House Orders
sheet into the in-app store. Because the sheet holds buyer names and dealer cost,
the most important tests here are the PII/cost-leak guards — a regression that
persists a customer name or invoice cost would push private data onto the public
website.
"""

import os
import sys

from tools import house_orders_sync as hos


def _row(**overrides):
    row = {
        "serial": "15677",
        "serial_2": "10508",
        "model": "Oak 28x56 (Marvel 4) 3/2",
        "manufacturer": "Tru Belton",
        "customer": None,  # blank = available
    }
    row.update(overrides)
    return row


class FakeDatabase:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def upsert_inventory(self, inventory_id: str, data: dict) -> str:
        self.docs[inventory_id] = data
        return inventory_id


# --- PII / cost leak guards (the critical ones) --------------------------------

def test_customer_name_sets_status_but_is_never_persisted():
    doc = hos.house_order_row_to_inventory_doc(_row(customer="Antonio Martinez"))
    assert doc["status"] == "SOLD"
    # The buyer's name must appear NOWHERE in the persisted record.
    blob = repr(doc).lower()
    assert "antonio" not in blob and "martinez" not in blob
    assert "customer" not in doc


def test_blank_customer_is_available():
    assert hos.house_order_row_to_inventory_doc(_row(customer=None))["status"] == "AVAILABLE"
    assert hos.house_order_row_to_inventory_doc(_row(customer="  "))["status"] == "AVAILABLE"


def test_invoice_cost_and_price_never_in_doc():
    # Even if the raw row carries cost/price-ish junk, the allow-list drops it.
    doc = hos.house_order_row_to_inventory_doc(
        {**_row(), "invoice_amount": 60187.0, "msrp": 97846.15, "salesman": "Adriana"}
    )
    for forbidden in ("invoice_amount", "msrp", "sale_price", "price", "salesman", "customer"):
        assert forbidden not in doc


def test_emitted_keys_are_within_the_allow_list():
    doc = hos.house_order_row_to_inventory_doc(_row(customer="Jane Buyer"))
    assert set(doc).issubset(hos._ALLOWED_KEYS)


# --- mapping correctness -------------------------------------------------------

def test_maps_specs_and_classification_from_model():
    doc = hos.house_order_row_to_inventory_doc(_row(model="Oak 28x56 3/2"))
    assert doc["serial_number"] == "15677"
    assert doc["model_name"] == "Oak 28x56 3/2"
    assert doc["manufacturer"] == "Tru Belton"
    assert doc["width"] == 28 and doc["length"] == 56
    assert doc["sqft"] == 28 * 56
    assert doc["bedrooms"] == 3 and doc["bathrooms"] == 2
    assert doc["classification"] == "Double Wide"  # width >= 28
    assert doc["is_new"] is True
    assert doc["source"] == hos.SYNC_SOURCE


def test_single_wide_classification():
    doc = hos.house_order_row_to_inventory_doc(_row(model="Glory Smart 14x76"))
    assert doc["classification"] == "Single Wide"  # width < 28


def test_iter_home_rows_skips_headers_sections_and_totals():
    records = [
        {"Serial #": "Serial #", "Model": "Model"},          # header echo
        {"Serial #": "On Approval", "Model": ""},            # section label
        {"Serial #": "total", "Model": "", "MSRP": 851620},  # totals row
        {"Serial #": "15677", "Model": "Oak 28x56", "Manufacturing Plant": "Tru Belton"},  # real
        {"Serial #": "nan", "Model": "nan"},                 # empty
    ]
    rows = list(hos._iter_home_rows(records))
    assert [r["serial"] for r in rows] == ["15677"]


def test_full_transform_from_sheet_records():
    records = [
        {"Serial #": "15677", "Model": "Oak 28x56 3/2", "Manufacturing Plant": "Tru Belton", "Customer": None},
        {"Serial #": "10543", "Model": "Jackson 16x76", "Manufacturing Plant": "Jessup", "Customer": "Gabriel Mercado"},
        {"Serial #": "Serial #", "Model": "Model"},  # dropped
    ]
    docs = hos.house_orders_to_inventory_docs(records)
    assert {d["serial_number"] for d in docs} == {"15677", "10543"}
    by_serial = {d["serial_number"]: d for d in docs}
    assert by_serial["15677"]["status"] == "AVAILABLE"
    assert by_serial["10543"]["status"] == "SOLD"
    assert "mercado" not in repr(docs).lower()  # no buyer name anywhere


# --- sync / idempotency --------------------------------------------------------

def test_sync_dry_run_writes_nothing():
    db = FakeDatabase()
    docs = hos.house_orders_to_inventory_docs([_sheet_rec("15677"), _sheet_rec("10543")])
    stats = hos.sync_house_orders_to_inventory(db, docs, dry_run=True)
    assert stats["total"] == 2 and stats["written"] == 0
    assert db.docs == {}


def test_sync_apply_upserts_by_serial_idempotently():
    db = FakeDatabase()
    docs = hos.house_orders_to_inventory_docs([_sheet_rec("15677")])
    first = hos.sync_house_orders_to_inventory(db, docs, dry_run=False)
    assert first["written"] == 1 and set(db.docs) == {"15677"}
    # Re-run: same serial doc id, no duplicate.
    second = hos.sync_house_orders_to_inventory(db, docs, dry_run=False)
    assert second["written"] == 1 and set(db.docs) == {"15677"}


def test_sync_counts_available_vs_sold():
    db = FakeDatabase()
    docs = hos.house_orders_to_inventory_docs([
        _sheet_rec("15677", customer=None),
        _sheet_rec("10543", customer="Some Buyer"),
    ])
    stats = hos.sync_house_orders_to_inventory(db, docs, dry_run=False)
    assert stats["available"] == 1 and stats["sold"] == 1


def _sheet_rec(serial, customer=None):
    return {
        "Serial #": serial,
        "Model": "Oak 28x56 3/2",
        "Manufacturing Plant": "Tru Belton",
        "Customer": customer,
    }


def test_load_house_orders_from_gcs_downloads_parses_and_cleans_up(monkeypatch, tmp_path):
    """The GCS loader downloads the xlsx, parses it, and removes the temp file."""
    import types
    captured = {}

    class _FakeBlob:
        def download_to_filename(self, path):
            captured["downloaded_to"] = path
            with open(path, "wb") as fh:
                fh.write(b"fake-xlsx-bytes")

    class _FakeBucket:
        def blob(self, name):
            captured["blob"] = name
            return _FakeBlob()

    class _FakeClient:
        def bucket(self, name):
            captured["bucket"] = name
            return _FakeBucket()

    fake_storage = types.SimpleNamespace(Client=lambda: _FakeClient())
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage)
    # parse the downloaded file via a stub (avoids needing a real xlsx)
    monkeypatch.setattr(
        hos, "parse_house_orders",
        lambda path: [{"serial_number": "X", "model_name": "M", "status": "AVAILABLE"}],
    )

    docs = hos.load_house_orders_from_gcs("tho-inventory-assets", "house-orders/House Orders.xlsx")

    assert captured["bucket"] == "tho-inventory-assets"
    assert captured["blob"] == "house-orders/House Orders.xlsx"
    assert docs == [{"serial_number": "X", "model_name": "M", "status": "AVAILABLE"}]
    assert not os.path.exists(captured["downloaded_to"])  # temp file cleaned up
