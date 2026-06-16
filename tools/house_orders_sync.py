"""Sync the live "House Orders" sheet into the in-app Firestore inventory.

House Orders.xlsx (Drive, owned by the THO operator, updated daily) is the real
source of truth for the dealer's stock — see the Command Center. The public
website was reading a frozen 2026-05-11 scrape instead; this sync makes the
in-app inventory store (served when ``INVENTORY_SOURCE=firestore``) reflect the
sheet.

Relationship to the existing tools:
- ``tools/convert_inventory.py`` already parses House Orders → inventory.json,
  but it carries ``invoice_amount`` (dealer COST) and hardcodes status=Available.
  This module is the PII/cost-safe path for the *public* store.

PII / sensitivity contract (the reason this is a separate module):
- The sheet's ``Customer`` column (a buyer NAME) is read ONLY to derive
  availability (blank = available stock, filled = sold/committed) — the name is
  **never** carried into a record or persisted.
- ``Invoice Amount`` (dealer cost) and ``MSRP`` (retail price) are **not**
  written: THO sells "Call for Price", and cost must never reach a public surface.
- Records are built by an explicit ALLOW-LIST (emit only known-safe keys), so a
  new sheet column can never silently leak into the public store.

Idempotent: upserts via ``firestore_client.upsert_inventory`` keyed on
``serial_number`` = Firestore doc id, like ``tools/inventory_seed.py``. Dry-run
by default; ``--apply`` (or ``dry_run=False``) writes.
"""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Iterable, Iterator
from typing import Any

log = logging.getLogger(__name__)

SYNC_SOURCE = "house_orders_sync"

# The "Customer" column is read only to set availability. Anything here means
# the home is committed to a buyer and must NOT appear on the public site.
_SOLD_STATUS = "SOLD"
_AVAILABLE_STATUS = "AVAILABLE"

# Keys we will ever persist. An allow-list (not a deny-list) so an unexpected
# sheet column can never leak. Note the absence of customer/invoice/salesman/price.
_ALLOWED_KEYS = frozenset({
    "serial_number", "serial_number_2", "model_name", "manufacturer",
    "classification", "status", "is_new",
    "bedrooms", "bathrooms", "sqft", "width", "length", "source",
})

# Sheet header / section labels that are not real inventory rows.
_NON_HOME_SERIAL_TOKENS = ("serial", "section", "approval", "days out", "wks", "nan", "total", "stock")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _pick(record: dict, *names: str) -> Any:
    """Case/space-insensitive column lookup across a few candidate names."""
    norm = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in record.items() if isinstance(k, str)}
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in norm:
            return norm[key]
    return None


def _is_home_serial(serial: str) -> bool:
    """A real home row has a serial that contains a digit and isn't a label."""
    if not serial:
        return False
    low = serial.lower()
    if any(tok in low for tok in _NON_HOME_SERIAL_TOKENS):
        return False
    return any(ch.isdigit() for ch in serial)


def _status_from_customer(customer: Any) -> str:
    """Blank customer => available stock; any name => sold/committed (name dropped)."""
    return _AVAILABLE_STATUS if not _clean(customer) else _SOLD_STATUS


def _parse_model_specs(model: str) -> tuple[int | None, int | None, int | None, int | None]:
    """Pull (width, length, beds, baths) out of a model string like 'Oak 28x56 3/2'.

    Hardened against junk: the dims regex is anchored to non-digits so a 3-digit
    run ('100x200') or a date doesn't slip in; width is sanity-bounded (8-40, a
    real section width) and de-transposed ('76x14' -> 14x76); beds/baths require
    single digits NOT inside a longer run so a date ('6/12/2024') is ignored.
    """
    width = length = beds = baths = None
    dim = re.search(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{2,3})(?!\d)", model)
    if dim:
        w, length_val = int(dim.group(1)), int(dim.group(2))
        if w > 40 and 8 <= length_val <= 40:  # written 'LxW' -> swap to WxL
            w, length_val = length_val, w
        if 8 <= w <= 40:
            width, length = w, length_val
    bb = re.search(r"(?<!\d)([1-9])\s*/\s*([1-9])(?!\d)", model)
    if bb:
        beds, baths = int(bb.group(1)), int(bb.group(2))
    return width, length, beds, baths


def _iter_home_rows(records: Iterable[dict]) -> Iterator[dict]:
    """Normalize raw sheet records to ``{serial, serial_2, model, manufacturer, customer}``.

    Filters out header/section/total rows and anything without a real serial or
    model. ``customer`` is kept ONLY so the caller can derive status; it is never
    emitted downstream.
    """
    for rec in records:
        serial = _clean(_pick(rec, "Serial #", "Serial #1", "Serial", "Serial Number"))
        if not _is_home_serial(serial):
            continue
        model = _clean(_pick(rec, "Model", "Model Name"))
        if not model or model.lower() in {"model", "nan"}:
            continue
        yield {
            "serial": serial,
            "serial_2": _clean(_pick(rec, "Serial #2", "Serial #1.1", "Serial 2")),
            "model": model,
            "manufacturer": _clean(_pick(rec, "Manufacturing Plant", "Manufacturer", "Plant")),
            "customer": _pick(rec, "Customer"),  # status only — never persisted
        }


def house_order_row_to_inventory_doc(row: dict) -> dict | None:
    """Map one normalized House Orders row to a PII/cost-free Firestore doc.

    Emits ONLY allow-listed keys. No customer name, no invoice cost, no price —
    the public site keeps "Call for Price". Returns None for non-home rows.
    """
    serial = _clean(row.get("serial"))
    model = _clean(row.get("model"))
    if not _is_home_serial(serial) or not model:
        return None
    width, length, beds, baths = _parse_model_specs(model)
    doc = {
        "serial_number": serial,
        "serial_number_2": _clean(row.get("serial_2")),
        "model_name": model,
        "manufacturer": _clean(row.get("manufacturer")) or "Unknown",
        "classification": "Double Wide" if (width or 0) >= 28 else "Single Wide",
        "status": _status_from_customer(row.get("customer")),
        "is_new": True,  # House Orders = new factory stock; repos live in the 21st Repo DB
        "bedrooms": beds,
        "bathrooms": baths,
        "sqft": (width * length) if (width and length) else None,
        "width": width,
        "length": length,
        "source": SYNC_SOURCE,
    }
    # ALLOW-LIST emit: drop empty values AND anything not explicitly permitted.
    return {k: v for k, v in doc.items() if k in _ALLOWED_KEYS and v not in (None, "")}


def house_orders_to_inventory_docs(records: Iterable[dict]) -> list[dict]:
    """Full transform: raw sheet records -> list of safe Firestore inventory docs."""
    docs = []
    for row in _iter_home_rows(records):
        doc = house_order_row_to_inventory_doc(row)
        if doc:
            docs.append(doc)
    return docs


def parse_house_orders(path: str) -> list[dict]:
    """Read House Orders.xlsx (sheet 'Everybody Else') into safe inventory docs."""
    import pandas as pd  # local: pandas only needed for the real-file path

    df = pd.read_excel(path, sheet_name="Everybody Else", header=1)
    records = df.to_dict("records")
    return house_orders_to_inventory_docs(records)


def load_house_orders_from_gcs(bucket_name: str, blob_name: str) -> list[dict]:
    """Download House Orders.xlsx from GCS to a temp file and parse it.

    Lets a daily Cloud Scheduler job (or a staff "refresh" action) keep the
    in-app inventory current from the operator's sheet without a manual local
    run. The run-as SA reads the bucket; nothing is persisted to disk beyond a
    short-lived temp file.
    """
    import os
    import tempfile

    from google.cloud import storage

    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        blob.download_to_filename(path)
        return parse_house_orders(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def sync_house_orders_to_inventory(
    db: Any, docs: list[dict], *, dry_run: bool = True, limit: int | None = None
) -> dict:
    """Upsert inventory docs into Firestore, keyed on serial_number = doc id.

    Idempotent. ``db`` must provide ``upsert_inventory(doc_id, data)``. Returns a
    stats dict; on ``dry_run`` nothing is written.
    """
    stats: dict[str, Any] = {"total": 0, "written": 0, "available": 0, "sold": 0, "planned": []}
    for doc in docs[: limit if limit else None]:
        stats["total"] += 1
        if doc["status"] == _AVAILABLE_STATUS:
            stats["available"] += 1
        else:
            stats["sold"] += 1
        stats["planned"].append((doc["serial_number"], doc["model_name"], doc["status"]))
        if dry_run:
            continue
        db.upsert_inventory(doc["serial_number"], doc)
        stats["written"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync House Orders.xlsx -> Firestore inventory.")
    parser.add_argument("--path", default="data/House Orders.xlsx", help="Path to House Orders.xlsx")
    parser.add_argument("--apply", action="store_true", help="Write to Firestore (default: dry run).")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    docs = parse_house_orders(args.path)
    db = None
    if args.apply:
        from database.firestore_client import get_database

        db = get_database()
    stats = sync_house_orders_to_inventory(db, docs, dry_run=not args.apply, limit=args.limit)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] homes: {stats['total']} | available: {stats['available']} | sold: {stats['sold']} "
          f"| written: {stats['written']}")
    if not args.apply:
        for serial, model, status in stats["planned"][:25]:
            print(f"  {status:9} {serial}: {model}")
        if len(stats["planned"]) > 25:
            print(f"  ... and {len(stats['planned']) - 25} more")
        print("\nRe-run with --apply to write. Then flip INVENTORY_SOURCE=firestore to serve.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
