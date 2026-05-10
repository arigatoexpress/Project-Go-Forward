"""
Allow-list sanitizer for THO House Orders spreadsheets.

House Orders.xlsx is useful for inventory/document autofill, but it can also
carry buyer, deal, financing, and free-text operational notes. This module keeps
the import path intentionally narrow:

- read workbook rows;
- keep only known inventory/document-autofill fields;
- report sensitive columns by name without copying their values;
- update existing Firestore inventory only after a dry-run diff.

It is deliberately separate from inventory_master_catalog.py, which should keep
rejecting House Orders-style files.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from numbers import Integral, Real
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pandas is required: pip install pandas openpyxl") from exc

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOG = logging.getLogger("house_orders_sanitizer")


def _normalize_col(name: str) -> str:
    """Normalize spreadsheet headers for tolerant alias matching."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower())
    return normalized.strip("_")


FIELD_ALIASES: dict[str, set[str]] = {
    "inventory_id": {
        "inventory_id",
        "legacy_inventory_id",
        "listing_id",
        "stock",
        "stock_no",
        "stock_number",
        "unit",
        "unit_id",
        "unit_identifier",
    },
    "serial_number": {
        "serial",
        "serial_1",
        "serial_no",
        "serial_num",
        "serial_number",
        "serial_number_1",
        "serial_sec_1",
        "serial_section_1",
        "sn",
        "vin",
        "vin_1",
    },
    "serial_number_2": {
        "serial_2",
        "serial_no_2",
        "serial_num_2",
        "serial_number_2",
        "serial_sec_2",
        "serial_section_2",
        "sn_2",
        "vin_2",
    },
    "label_number": {
        "hud",
        "hud_1",
        "hud_label",
        "hud_label_1",
        "label",
        "label_1",
        "label_number",
        "label_number_1",
        "label_seal",
        "label_seal_1",
    },
    "label_number_2": {
        "hud_2",
        "hud_label_2",
        "label_2",
        "label_number_2",
        "label_seal_2",
    },
    "model_name": {
        "home_model",
        "model",
        "model_name",
        "model_no",
        "model_number",
    },
    "manufacturer": {
        "factory",
        "factory_name",
        "manufacturer",
        "manufacturer_plant",
        "mfg",
        "mfr",
        "plant",
        "plant_name",
    },
    "year": {"home_year", "model_year", "year"},
    "invoice_date": {
        "inventory_date",
        "invoice_date",
        "order_date",
        "purchase_date",
    },
    "invoice_amount": {
        "factory_invoice",
        "invoice",
        "invoice_amount",
        "invoice_price",
    },
    "msrp": {"list_price", "msrp", "retail_price"},
    "date_of_manufacture": {
        "date_const",
        "date_manufactured",
        "date_of_manufacture",
        "manufacture_date",
        "manufactured_date",
        "mfg_date",
    },
    "wind_zone": {"wind", "wind_zone", "wind_zone_1"},
    "thermal_zone": {"thermal", "thermal_zone"},
    "sections": {
        "no_of_sections",
        "number_of_sections",
        "section_count",
        "sections",
    },
    "width": {"home_width", "total_size_w", "width"},
    "length": {"home_length", "length", "total_size_l"},
    "sqft": {"sq_ft", "sqft", "square_feet", "total_sqft"},
    "weight_sec_1": {
        "section_1_weight",
        "section_one_weight",
        "weight1",
        "weight_1",
        "weight_sec1",
        "weight_sec_1",
    },
    "weight_sec_2": {
        "section_2_weight",
        "section_two_weight",
        "weight2",
        "weight_2",
        "weight_sec2",
        "weight_sec_2",
    },
    "manufacturer_address": {
        "factory_address",
        "manufacturer_address",
        "plant_address",
    },
    "manufacturer_city": {"factory_city", "manufacturer_city", "plant_city"},
    "manufacturer_state": {"factory_state", "manufacturer_state", "plant_state"},
    "manufacturer_zip": {
        "factory_zip",
        "manufacturer_zip",
        "manufacturer_zip_code",
        "plant_zip",
    },
    "_dimensions": {
        "dimensions",
        "home_size",
        "size",
        "total_size",
    },
}

ALIAS_TO_FIELD = {alias: field for field, aliases in FIELD_ALIASES.items() for alias in aliases}

WRITABLE_SAFE_FIELDS: set[str] = set(FIELD_ALIASES) - {"_dimensions"}
TEXT_FIELDS = WRITABLE_SAFE_FIELDS - {
    "invoice_amount",
    "msrp",
    "year",
    "sections",
    "width",
    "length",
    "sqft",
    "weight_sec_1",
    "weight_sec_2",
    "invoice_date",
    "date_of_manufacture",
}
MONEY_FIELDS = {"invoice_amount", "msrp"}
INT_FIELDS = {"year", "sections", "width", "length", "sqft", "weight_sec_1", "weight_sec_2"}
DATE_FIELDS = {"invoice_date", "date_of_manufacture"}
PATCH_ONLY_FIELDS = WRITABLE_SAFE_FIELDS - {"inventory_id"}

SENSITIVE_TOKENS = {
    "applicant",
    "approval",
    "approved",
    "buyer",
    "co_buyer",
    "cobuyer",
    "comments",
    "credit",
    "customer",
    "dead",
    "deal",
    "denial",
    "denied",
    "dob",
    "email",
    "employer",
    "employment",
    "finance",
    "financing",
    "income",
    "lender",
    "loan",
    "note",
    "notes",
    "phone",
    "salesman",
    "salesperson",
    "salesrep",
    "ssn",
}


@dataclass(frozen=True)
class SanitizedHouseOrderRow:
    row_number: int
    data: dict[str, Any]
    source_columns: dict[str, str]
    warnings: list[str]

    @property
    def stable_identifiers(self) -> dict[str, str]:
        identifiers: dict[str, str] = {}
        for field in ("inventory_id", "serial_number", "serial_number_2", "model_name"):
            value = self.data.get(field)
            if value is not None and str(value).strip():
                identifiers[field] = str(value).strip()
        return identifiers

    def preview(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "stable_identifiers": self.stable_identifiers,
            "fields": dict(sorted(self.data.items())),
            "source_columns": dict(sorted(self.source_columns.items())),
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class InventoryPatchPlan:
    row_number: int
    match_status: str
    inventory_id: str | None
    match_field: str | None
    match_value: str | None
    model_name: str | None
    patch: dict[str, Any]
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "match_status": self.match_status,
            "inventory_id": self.inventory_id,
            "match_field": self.match_field,
            "match_value": self.match_value,
            "model_name": self.model_name,
            "patch": dict(sorted(self.patch.items())),
            "skipped_reason": self.skipped_reason,
        }


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _clean_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    if isinstance(value, Integral) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        if number.is_integer():
            return str(int(number))
    text = str(value).strip()
    return text or None


def _to_float(value: Any) -> float | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return float(re.sub(r"[$,]", "", text))
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return int(float(re.sub(r"[,]", "", text)))
    except ValueError:
        return None


def _to_date_iso(value: Any) -> str | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = pd.to_datetime(value, errors="coerce")
    if _is_missing(parsed):
        return None
    return parsed.date().isoformat()


def _parse_dimensions(value: Any) -> tuple[int | None, int | None]:
    text = _clean_text(value)
    if text is None:
        return None, None
    match = re.search(r"(\d{2,3})\s*(?:x|by|/)\s*(\d{2,3})", text, flags=re.IGNORECASE)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _normalize_model_key(value: Any) -> str:
    text = _clean_text(value) or ""
    if "/" in text:
        text = text.split("/")[-1]
    text = text.strip().lower()
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _coerce_field(field: str, value: Any) -> Any | None:
    if field in MONEY_FIELDS:
        return _to_float(value)
    if field in INT_FIELDS:
        return _to_int(value)
    if field in DATE_FIELDS:
        return _to_date_iso(value)
    return _clean_text(value)


def _column_maps(columns: list[str]) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (safe field -> source column, sensitive columns, ignored columns)."""
    mapped: dict[str, str] = {}
    sensitive: list[str] = []
    ignored: list[str] = []

    for col in columns:
        norm = _normalize_col(col)
        field = ALIAS_TO_FIELD.get(norm)
        if field:
            mapped.setdefault(field, col)
            continue
        if _looks_sensitive_header(norm):
            sensitive.append(col)
        else:
            ignored.append(col)
    return mapped, sensitive, ignored


def _looks_sensitive_header(norm: str) -> bool:
    parts = set(norm.split("_"))
    if parts & SENSITIVE_TOKENS:
        return True
    return any(token in norm for token in ("customer", "credit", "ssn", "lender", "loan"))


def _detect_header_index(raw: pd.DataFrame) -> int:
    """Find the most likely header row in a workbook with title/preamble rows."""
    best_index = 0
    best_score = -1

    for index, row in raw.iterrows():
        norms = [_normalize_col(value) for value in row.tolist() if not _is_missing(value)]
        safe_count = sum(1 for norm in norms if norm in ALIAS_TO_FIELD)
        sensitive_count = sum(1 for norm in norms if _looks_sensitive_header(norm))
        score = (safe_count * 10) + min(sensitive_count, 3)
        if score > best_score:
            best_score = score
            best_index = int(index)

    return best_index


def parse_house_orders_dataframe(
    df: pd.DataFrame,
) -> tuple[list[SanitizedHouseOrderRow], dict[str, Any]]:
    mapped_columns, sensitive_columns, ignored_columns = _column_maps([str(c) for c in df.columns])
    rows: list[SanitizedHouseOrderRow] = []

    for index, row in df.iterrows():
        data: dict[str, Any] = {}
        source_columns: dict[str, str] = {}
        warnings: list[str] = []

        for field, column in mapped_columns.items():
            if field == "_dimensions":
                continue
            value = _coerce_field(field, row[column])
            if value is not None and str(value).strip() != "":
                data[field] = value
                source_columns[field] = column

        if ("width" not in data or "length" not in data) and "_dimensions" in mapped_columns:
            width, length = _parse_dimensions(row[mapped_columns["_dimensions"]])
            if width is not None and "width" not in data:
                data["width"] = width
                source_columns["width"] = mapped_columns["_dimensions"]
            if length is not None and "length" not in data:
                data["length"] = length
                source_columns["length"] = mapped_columns["_dimensions"]

        if not data:
            continue

        has_write_match = any(data.get(field) for field in ("inventory_id", "serial_number"))
        if not has_write_match:
            warnings.append(
                "No inventory_id or primary serial_number; apply will skip unless matched manually."
            )

        rows.append(
            SanitizedHouseOrderRow(
                row_number=int(index) + 2,
                data=data,
                source_columns=source_columns,
                warnings=warnings,
            )
        )

    metadata = {
        "source_rows": int(len(df.index)),
        "sanitized_rows": len(rows),
        "safe_columns": dict(sorted(mapped_columns.items())),
        "sensitive_columns_dropped": sorted(sensitive_columns),
        "ignored_columns": sorted(ignored_columns),
        "safe_fields": sorted(WRITABLE_SAFE_FIELDS),
    }
    return rows, metadata


def build_sanitized_report(
    rows: list[SanitizedHouseOrderRow], metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "success": True,
        **metadata,
        "records": [row.preview() for row in rows],
    }


def _values_equal(existing: Any, proposed: Any) -> bool:
    if existing is None and proposed is None:
        return True
    return str(existing).strip() == str(proposed).strip()


def build_firestore_patch_plan(
    rows: list[SanitizedHouseOrderRow],
    db: Any,
    allow_model_match: bool = False,
) -> list[InventoryPatchPlan]:
    plans: list[InventoryPatchPlan] = []
    model_index = _build_model_index(db) if allow_model_match else {}

    for row in rows:
        existing: dict[str, Any] | None = None
        match_field: str | None = None
        match_value: str | None = None

        inventory_id = row.data.get("inventory_id")
        if inventory_id:
            match_field = "inventory_id"
            match_value = str(inventory_id)
            existing = db.get_inventory_by_id(match_value)
            if existing:
                existing = {**existing, "id": existing.get("id") or match_value}

        if existing is None and row.data.get("serial_number"):
            match_field = "serial_number"
            match_value = str(row.data["serial_number"])
            existing = db.get_inventory_by_serial(match_value)

        if existing is None and allow_model_match and row.data.get("model_name"):
            model_key = _normalize_model_key(row.data["model_name"])
            candidate = model_index.get(model_key)
            if candidate:
                match_field = "model_name"
                match_value = str(row.data["model_name"])
                existing = candidate

        if existing is None:
            plans.append(
                InventoryPatchPlan(
                    row_number=row.row_number,
                    match_status="missing_match",
                    inventory_id=None,
                    match_field=match_field,
                    match_value=match_value,
                    model_name=row.data.get("model_name"),
                    patch={},
                    skipped_reason=(
                        "No existing inventory record matched inventory_id, primary serial_number"
                        + (", or unique model_name." if allow_model_match else ".")
                    ),
                )
            )
            continue

        patch = {
            field: value
            for field, value in row.data.items()
            if field in PATCH_ONLY_FIELDS
            and not (match_field == "model_name" and field == "model_name")
            and not _values_equal(existing.get(field), value)
        }

        if not patch:
            plans.append(
                InventoryPatchPlan(
                    row_number=row.row_number,
                    match_status="no_changes",
                    inventory_id=str(existing.get("id") or ""),
                    match_field=match_field,
                    match_value=match_value,
                    model_name=row.data.get("model_name") or existing.get("model_name"),
                    patch={},
                    skipped_reason="Existing inventory already has the sanitized values.",
                )
            )
            continue

        plans.append(
            InventoryPatchPlan(
                row_number=row.row_number,
                match_status="matched",
                inventory_id=str(existing.get("id") or ""),
                match_field=match_field,
                match_value=match_value,
                model_name=row.data.get("model_name") or existing.get("model_name"),
                patch=patch,
            )
        )

    return plans


def _build_model_index(db: Any) -> dict[str, dict[str, Any]]:
    inventory = db.search_inventory(status=None, limit=5000)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in inventory:
        key = _normalize_model_key(item.get("model_name") or item.get("model"))
        if key:
            buckets.setdefault(key, []).append(item)
    return {key: items[0] for key, items in buckets.items() if len(items) == 1}


def apply_firestore_patch_plan(plans: list[InventoryPatchPlan], db: Any) -> dict[str, int]:
    counts = {"updated": 0, "skipped": 0, "failed": 0}
    for plan in plans:
        if plan.match_status != "matched" or not plan.inventory_id or not plan.patch:
            counts["skipped"] += 1
            continue
        try:
            db.update_inventory(plan.inventory_id, plan.patch)
            counts["updated"] += 1
        except Exception as exc:  # pragma: no cover - exercised with live Firestore failures
            LOG.error("Failed to update inventory_id=%s: %s", plan.inventory_id, exc)
            counts["failed"] += 1
    return counts


def read_house_orders_xlsx(
    path: Path,
    sheet_name: str | int | None = 0,
    header_row: int | str = "auto",
) -> pd.DataFrame:
    if header_row == "auto":
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=50)
        header = _detect_header_index(raw)
    else:
        header = int(header_row)

    df = pd.read_excel(path, sheet_name=sheet_name, header=header)
    df.attrs["house_orders_header_row"] = header
    return df


def _write_or_print(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n")
        print(f"Wrote {output}")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sanitize House Orders.xlsx into inventory/document autofill patches."
    )
    parser.add_argument("--xlsx", required=True, type=Path, help="Path to House Orders.xlsx")
    parser.add_argument("--sheet", default=0, help="Excel sheet name or zero-based index")
    parser.add_argument(
        "--header-row",
        default="auto",
        help="Excel header row, zero-based, or 'auto' for title/preamble workbooks",
    )
    parser.add_argument("--output", type=Path, help="Write JSON report to this path")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only")
    parser.add_argument(
        "--compare-firestore",
        action="store_true",
        help="Read Firestore and include a patch plan diff in dry-run output",
    )
    parser.add_argument(
        "--allow-model-match",
        action="store_true",
        help="Opt in to exact unique model-name matches when serial/ID are absent in Firestore",
    )
    parser.add_argument("--apply", action="store_true", help="Apply matched safe patches")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    if not (args.dry_run or args.apply):
        parser.error("Pass --dry-run or --apply.")
    if args.dry_run and args.apply:
        parser.error("Use either --dry-run or --apply, not both.")
    if not args.xlsx.exists():
        parser.error(f"File not found: {args.xlsx}")

    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose >= 2
        else (logging.INFO if args.verbose else logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    sheet: str | int | None
    sheet = int(args.sheet) if str(args.sheet).isdigit() else args.sheet
    header_row: int | str = args.header_row
    if header_row != "auto":
        header_row = int(header_row)
    df = read_house_orders_xlsx(args.xlsx, sheet_name=sheet, header_row=header_row)
    rows, metadata = parse_house_orders_dataframe(df)
    report = build_sanitized_report(rows, metadata)
    report["header_row"] = df.attrs.get("house_orders_header_row")

    if args.compare_firestore or args.apply:
        from database.firestore_client import THODatabase

        db = THODatabase()
        plans = build_firestore_patch_plan(rows, db, allow_model_match=args.allow_model_match)
        report["patch_plan"] = [plan.to_dict() for plan in plans]
        report["patch_summary"] = {
            "matched": sum(1 for plan in plans if plan.match_status == "matched"),
            "missing_match": sum(1 for plan in plans if plan.match_status == "missing_match"),
            "no_changes": sum(1 for plan in plans if plan.match_status == "no_changes"),
        }
        if args.apply:
            report["apply_summary"] = apply_firestore_patch_plan(plans, db)

    _write_or_print(report, args.output)
    failed = report.get("apply_summary", {}).get("failed", 0)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
