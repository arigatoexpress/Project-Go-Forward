"""
inventory_master_catalog.py
===========================

Strict-schema ingest for the manufacturer master catalog ("all homes we
can order", ~270 plans per Mark Willcott's Apr 2026 follow-up). Writes
to Firestore via :class:`database.firestore_client.THODatabase` and
validates each row against :class:`database.models.Inventory` so the
PR #20 write-time guard catches malformed rows before they hit the DB.

**Deliberately rejects** the columns present in ``House Orders.xlsx``
that contain customer PII (Customer name, Customer #, Salesman,
free-text notes about credit / employment / health). Anything that
looks like operational customer data ERRORS, not silently dropped, so
the wrong file fails loudly.

Expected master-catalog columns (case-insensitive; spaces & hyphens
normalize to underscore):

* ``Manufacturer`` — must match an entry in MANUFACTURER_ALIASES.
* ``Model``        — non-empty (mapped to ``Inventory.model_name``).
* ``Serial`` / ``Serial #`` / ``Plan ID`` — primary key
  (mapped to ``Inventory.serial_number``).
* ``Width`` and ``Length`` (in feet) *or* a single ``Size`` like ``32x60``.
* ``MSRP`` — numeric.
* Optional: ``Bedrooms``, ``Bathrooms``, ``SqFt``, ``Status``, ``Notes``.

Usage
-----

    python3 tools/inventory_master_catalog.py --xlsx path/to/master.xlsx --dry-run
    python3 tools/inventory_master_catalog.py --xlsx path/to/master.xlsx --apply
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from numbers import Integral, Real
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pandas is required: pip install pandas openpyxl") from exc

# Add project root to path when invoked as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from database.models import Inventory, InventoryStatus  # noqa: E402

LOG = logging.getLogger("inventory_master_catalog")

# ---------------------------------------------------------------------------
# Whitelist & deny-list
# ---------------------------------------------------------------------------

#: Columns we accept (lowercased; spaces/hyphens normalized to underscore).
ACCEPTED_COLUMNS: set[str] = {
    "manufacturer",
    "model",
    "model_name",
    "serial",
    "serial_#",
    "serial_number",
    "plan_id",
    "width",
    "length",
    "size",
    "msrp",
    "invoice_amount",
    "bedrooms",
    "bathrooms",
    "beds",
    "baths",
    "sqft",
    "square_feet",
    "status",
    "notes",
}

#: Columns we explicitly reject — presence of any of these means the file
#: contains operational/customer data and is NOT a clean catalog.
PII_REJECT_COLUMNS: set[str] = {
    "customer",
    "customer_#",
    "customer_name",
    "salesman",
    "salesperson",
    "comments",
    "credit_score",
    "credit",
    "deposit",
    "denial",
    "ssn",
    "dob",
    "loan",
    "loan_status",
    "lender",
    "phone",
    "email",
    "address",
}

#: Aliases for the Manufacturer column. Match is case-insensitive on the
#: stripped & whitespace-collapsed value.
MANUFACTURER_ALIASES: dict[str, str] = {
    "cavco": "cavco",
    "champion la": "champion_la",
    "champion louisiana": "champion_la",
    "champion": "champion_la",
    "clayton": "clayton_ebuilt",
    "clayton ebuilt": "clayton_ebuilt",
    "clayton ss": "clayton_ebuilt",
    "jessup": "jessup",
    "jessup housing": "jessup",
    "legacy": "legacy",
    "legacy housing": "legacy",
    "new vision": "new_vision",
    "new vision manufacturing": "new_vision",
    "skyline kansas": "skyline_ks",
    "skyline louisiana": "skyline_la",
    "skyline ks": "skyline_ks",
    "skyline la": "skyline_la",
    "tru": "trumh",
    "tru belton": "trumh",
    "tru alabama": "trumh",
    "trumh": "trumh",
    "waco ii schult": "waco_ii",
    "waco": "waco_ii",
}


@dataclass(frozen=True)
class CatalogRow:
    """One validated row, ready to convert to an :class:`Inventory` instance."""

    serial_number: str
    manufacturer_key: str
    model_name: str
    width: int | None
    length: int | None
    msrp: float | None
    bedrooms: int | None
    bathrooms: int | None
    sqft: int | None
    status: InventoryStatus
    notes: str | None

    def to_inventory(self) -> Inventory:
        """Build a Pydantic :class:`Inventory` (raises if invalid)."""
        return Inventory(
            serial_number=self.serial_number,
            model_name=self.model_name,
            manufacturer=self.manufacturer_key,
            msrp=self.msrp,
            bedrooms=self.bedrooms,
            bathrooms=self.bathrooms,
            sqft=self.sqft,
            width=self.width,
            length=self.length,
            status=self.status,
            notes=self.notes,
        )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_col(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _clean_text(value: Any) -> str | None:
    """Normalize spreadsheet scalar values without adding pandas float suffixes."""
    if _is_missing(value):
        return None
    if isinstance(value, Integral) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        if number.is_integer():
            return str(int(number))
    return str(value).strip()


def _parse_size(value: Any) -> tuple[int | None, int | None]:
    if _is_missing(value):
        return None, None
    m = re.match(r"^(\d{2,3})\s*[xX]\s*(\d{2,3})$", str(value).strip())
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _resolve_manufacturer(value: Any) -> str:
    if _is_missing(value):
        raise ValueError("manufacturer is empty")
    key = re.sub(r"\s+", " ", str(value).strip().lower())
    if key in MANUFACTURER_ALIASES:
        return MANUFACTURER_ALIASES[key]
    raise ValueError(f"Unknown manufacturer: {value!r}")


def _parse_status(value: Any) -> InventoryStatus:
    if _is_missing(value):
        return InventoryStatus.AVAILABLE
    raw = str(value).strip().lower()
    for s in InventoryStatus:
        if raw == s.value.lower() or raw == s.name.lower():
            return s
    LOG.debug("Unknown status %r, defaulting to AVAILABLE", value)
    return InventoryStatus.AVAILABLE


def _to_int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        # Strip $ and commas if user pasted "$45,900" style.
        return float(re.sub(r"[$,]", "", str(value)))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_columns(df: pd.DataFrame) -> None:
    """Reject any spreadsheet that has operational/customer columns."""
    cols = {_normalize_col(c) for c in df.columns}
    forbidden = cols & PII_REJECT_COLUMNS
    if forbidden:
        raise ValueError(
            "Spreadsheet contains forbidden PII/operational columns: "
            f"{sorted(forbidden)}. This file is NOT a clean master catalog "
            "(it looks like House Orders.xlsx or similar). Ask Mark for a "
            "scrubbed catalog with manufacturer/model/serial/size/msrp only."
        )
    extra = cols - ACCEPTED_COLUMNS
    if extra:
        LOG.warning("Ignoring unexpected columns: %s", sorted(extra))


def parse_rows(df: pd.DataFrame) -> list[CatalogRow]:
    rows: list[CatalogRow] = []
    norm = {col: _normalize_col(col) for col in df.columns}

    def get(row, *names) -> Any:
        for n in names:
            for orig, normed in norm.items():
                if normed == n:
                    val = row[orig]
                    if not _is_missing(val):
                        return val
        return None

    for i, row in df.iterrows():
        serial_raw = get(row, "serial_number", "serial_#", "serial", "plan_id")
        serial = _clean_text(serial_raw)
        if not serial:
            LOG.debug("Row %d: skipping (no serial)", i)
            continue
        try:
            mfr = _resolve_manufacturer(get(row, "manufacturer"))
            model = _clean_text(get(row, "model_name", "model")) or ""
            if not model:
                raise ValueError("model is empty")

            width = _to_int(get(row, "width"))
            length = _to_int(get(row, "length"))
            if width is None or length is None:
                w2, l2 = _parse_size(get(row, "size"))
                width = width if width is not None else w2
                length = length if length is not None else l2

            rows.append(
                CatalogRow(
                    serial_number=serial,
                    manufacturer_key=mfr,
                    model_name=model,
                    width=width,
                    length=length,
                    msrp=_to_float(get(row, "msrp", "invoice_amount")),
                    bedrooms=_to_int(get(row, "bedrooms", "beds")),
                    bathrooms=_to_int(get(row, "bathrooms", "baths")),
                    sqft=_to_int(get(row, "sqft", "square_feet")),
                    status=_parse_status(get(row, "status")),
                    notes=_clean_text(get(row, "notes")),
                )
            )
        except Exception as exc:
            LOG.error("Row %d (serial=%r) FAILED validation: %s", i, serial_raw, exc)
            raise

    return rows


# ---------------------------------------------------------------------------
# Firestore write (THODatabase singleton, upsert by serial_number)
# ---------------------------------------------------------------------------


def write_to_firestore(rows: list[CatalogRow], dry_run: bool) -> dict[str, int]:
    """Upsert each row by serial_number. Returns {created, updated, failed}."""
    if dry_run:
        for r in rows[:10]:
            print(
                f"DRY  {r.manufacturer_key:14s}  {r.serial_number:14s}  "
                f"{r.model_name}  ({r.width or '?'}x{r.length or '?'})  "
                f"${r.msrp or 0:,.0f}"
            )
        if len(rows) > 10:
            print(f"... and {len(rows) - 10} more rows")
        return {"created": 0, "updated": 0, "failed": 0, "would_write": len(rows)}

    # Late import so --dry-run doesn't require firestore creds.
    from database.firestore_client import THODatabase

    db = THODatabase()
    counts = {"created": 0, "updated": 0, "failed": 0}
    for r in rows:
        try:
            inv_dict = r.to_inventory().model_dump(
                mode="json",
                exclude={"id"},
                exclude_none=True,
            )
            existing = db.get_inventory_by_serial(r.serial_number)
            if existing:
                db.update_inventory(existing["id"], inv_dict)
                counts["updated"] += 1
            else:
                db.create_inventory(inv_dict)
                counts["created"] += 1
        except Exception as exc:
            LOG.error("Failed to upsert serial=%s: %s", r.serial_number, exc)
            counts["failed"] += 1
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate + ingest the master catalog xlsx")
    p.add_argument("--xlsx", required=True, type=Path, help="Path to the master catalog xlsx")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args(argv)

    if not (args.dry_run or args.apply):
        p.error("Pass --dry-run or --apply.")

    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose >= 2
        else (logging.INFO if args.verbose else logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.xlsx.exists():
        p.error(f"File not found: {args.xlsx}")

    df = pd.read_excel(args.xlsx)
    validate_columns(df)
    rows = parse_rows(df)
    print(f"Parsed {len(rows)} rows from {args.xlsx}")

    by_mfr: dict[str, int] = {}
    for r in rows:
        by_mfr[r.manufacturer_key] = by_mfr.get(r.manufacturer_key, 0) + 1
    for k, v in sorted(by_mfr.items()):
        print(f"  {k:18s}  {v} plans")

    counts = write_to_firestore(rows, dry_run=args.dry_run)
    print(f"\n{counts}")
    return 0 if counts.get("failed", 0) == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
