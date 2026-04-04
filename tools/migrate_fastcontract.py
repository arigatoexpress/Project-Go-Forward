#!/usr/bin/env python3
"""Migrate 1,994 customer records from FastContract CSV to Firestore.

Source: THO_MASTER/full_migration_export.csv (FastContract legacy platform)
Target: Firestore 'customers' collection in sapphire-479610

Usage:
    python3 tools/migrate_fastcontract.py                    # Dry run (validate only)
    python3 tools/migrate_fastcontract.py --load             # Load to Firestore
    python3 tools/migrate_fastcontract.py --load --json      # Also save as local JSON
    python3 tools/migrate_fastcontract.py --report           # Show migration report
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

CSV_PATH = Path.home() / "Documents" / "Business" / "Kadima Digital Strategies 2026" / "THO_MASTER" / "full_migration_export.csv"
OUTPUT_JSON = Path(__file__).parent.parent / "data" / "migrated_customers.json"
REPORT_PATH = Path(__file__).parent.parent / "data" / "migration_report.json"

# FastContract status → THO status mapping
STATUS_MAP = {
    "contract": "ENROLLED",
    "archived": "CLOSED",
    "pending": "LEAD",
    "approved": "ENROLLED",
    "funded": "SOLD",
    "complete": "SOLD",
    "cancelled": "CLOSED",
    "denied": "CLOSED",
}

# Known test records to skip
TEST_NAMES = {"jermaine test", "test test", "test user", "jane doe", "john doe"}


def clean_phone(raw: str) -> str | None:
    """Normalize phone number to digits only, return None if invalid."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:10]}" if len(digits) >= 10 else None


def clean_email(raw: str) -> str | None:
    """Validate and lowercase email."""
    if not raw or not raw.strip():
        return None
    email = raw.strip().lower()
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return email
    return None


def mask_ssn(ssn: str) -> str:
    """Hash SSN for storage — never store plaintext."""
    if not ssn or not ssn.strip():
        return ""
    cleaned = re.sub(r"\D", "", ssn)
    if len(cleaned) < 4:
        return ""
    # Store last 4 + hash of full for lookup
    return f"***-**-{cleaned[-4:]}"


def ssn_hash(ssn: str) -> str:
    """Create deterministic hash of SSN for dedup/lookup without storing plaintext."""
    if not ssn or not ssn.strip():
        return ""
    cleaned = re.sub(r"\D", "", ssn)
    if len(cleaned) < 4:
        return ""
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


def parse_row(row: dict) -> dict | None:
    """Parse a FastContract CSV row into a THO customer record."""
    first = (row.get("Buyer_First_Name") or "").strip()
    last = (row.get("Buyer_Last_Name") or "").strip()
    full_name = f"{first} {last}".strip()

    if not full_name or len(full_name) < 2:
        return None

    # Skip test records
    if full_name.lower() in TEST_NAMES:
        return None

    # Map status
    raw_status = (row.get("ChangeAppStatus") or "").strip().lower()
    status = STATUS_MAP.get(raw_status, "LEAD")

    # Build address
    addr_parts = [
        (row.get("Mailing_Address") or "").strip(),
    ]
    city = (row.get("Mailing_City") or "").strip()
    state = (row.get("Mailing_State") or "TX").strip()
    zipcode = (row.get("Mailing_Zip") or "").strip()
    address = ", ".join(filter(None, addr_parts))
    if city:
        address += f", {city}"
    if state:
        address += f", {state}"
    if zipcode:
        address += f" {zipcode}"

    # Co-buyer
    co_first = (row.get("CoBuyer_First_Name") or "").strip()
    co_last = (row.get("CoBuyer_Last_Name") or "").strip()
    co_buyer = None
    if co_first or co_last:
        co_buyer = {
            "full_name": f"{co_first} {co_last}".strip(),
            "employer": (row.get("CoBuyer_Employer_Name") or "").strip() or None,
            "occupation": (row.get("CoBuyer_Occupation") or "").strip() or None,
            "phone": clean_phone(row.get("CoBuyer_Work_Phone1") or ""),
            "ssn_masked": mask_ssn(row.get("SSN2") or ""),
            "ssn_hash": ssn_hash(row.get("SSN2") or ""),
        }

    # References
    references = []
    for i in [1, 2]:
        ref_name = (row.get(f"Reference{i}_Name") or "").strip()
        ref_phone = clean_phone(row.get(f"Reference{i}_Phone") or "")
        if ref_name:
            references.append({"name": ref_name, "phone": ref_phone})

    return {
        "id": str(uuid.uuid4()),
        "legacy_id": (row.get("AppID") or "").strip(),
        "legacy_source": "fastcontract",
        "full_name": full_name,
        "email": clean_email(row.get("Buyer_Email") or ""),
        "phone": clean_phone(row.get("Phone1") or ""),
        "phone2": clean_phone(row.get("Phone2") or ""),
        "status": status,
        "address": address if address.strip(", ") else None,
        "city": city or None,
        "state": state or "TX",
        "zip_code": zipcode or None,
        "employer": (row.get("Employer_Name") or "").strip() or None,
        "occupation": (row.get("Occupation") or "").strip() or None,
        "marital_status": (row.get("Buyer_Marital_Status") or "").strip() or None,
        "self_employed": (row.get("Buyer_Self_Emp") or "").strip().upper() == "Y",
        "ssn_masked": mask_ssn(row.get("SSN1") or ""),
        "ssn_hash": ssn_hash(row.get("SSN1") or ""),
        "salesrep": (row.get("Salesrep") or "").strip() or None,
        "notes": (row.get("Notes") or "").strip() or None,
        "co_buyer": co_buyer,
        "references": references,
        "mailing_own_rent": (row.get("Mailing_Own") or "").strip() or None,
        "mailing_length_years": int(row.get("Mailing_Length") or 0) if (row.get("Mailing_Length") or "").isdigit() else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "migrated_at": datetime.now(timezone.utc).isoformat(),
    }


def migrate(load_to_firestore: bool = False, save_json: bool = False) -> dict:
    """Run the full migration pipeline."""
    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}")
        sys.exit(1)

    print(f"Reading {CSV_PATH}...")
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Found {len(rows)} raw records")

    # Parse and validate
    customers = []
    skipped = []
    errors = []
    seen_ids = set()

    for i, row in enumerate(rows):
        try:
            customer = parse_row(row)
            if customer is None:
                skipped.append({"row": i + 2, "reason": "empty name or test record", "name": f"{row.get('Buyer_First_Name', '')} {row.get('Buyer_Last_Name', '')}"})
                continue

            # Deduplicate by legacy_id
            if customer["legacy_id"] in seen_ids:
                skipped.append({"row": i + 2, "reason": "duplicate AppID", "id": customer["legacy_id"]})
                continue
            seen_ids.add(customer["legacy_id"])

            customers.append(customer)
        except Exception as e:
            errors.append({"row": i + 2, "error": str(e)})

    # Stats
    with_email = sum(1 for c in customers if c["email"])
    with_phone = sum(1 for c in customers if c["phone"])
    with_ssn = sum(1 for c in customers if c["ssn_masked"])
    with_address = sum(1 for c in customers if c["address"])
    with_co_buyer = sum(1 for c in customers if c["co_buyer"])

    status_counts = {}
    for c in customers:
        status_counts[c["status"]] = status_counts.get(c["status"], 0) + 1

    salesrep_counts = {}
    for c in customers:
        rep = c["salesrep"] or "Unknown"
        salesrep_counts[rep] = salesrep_counts.get(rep, 0) + 1

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": str(CSV_PATH),
        "raw_records": len(rows),
        "imported": len(customers),
        "skipped": len(skipped),
        "errors": len(errors),
        "stats": {
            "with_email": with_email,
            "with_phone": with_phone,
            "with_ssn": with_ssn,
            "with_address": with_address,
            "with_co_buyer": with_co_buyer,
        },
        "status_breakdown": status_counts,
        "salesrep_breakdown": dict(sorted(salesrep_counts.items(), key=lambda x: -x[1])),
        "skipped_details": skipped[:20],
        "error_details": errors[:20],
    }

    print(f"\n{'='*60}")
    print(f"  MIGRATION REPORT")
    print(f"{'='*60}")
    print(f"  Raw records:  {len(rows)}")
    print(f"  Imported:     {len(customers)}")
    print(f"  Skipped:      {len(skipped)}")
    print(f"  Errors:       {len(errors)}")
    print(f"\n  Coverage:")
    print(f"    With email:    {with_email} ({with_email*100//len(customers)}%)")
    print(f"    With phone:    {with_phone} ({with_phone*100//len(customers)}%)")
    print(f"    With SSN:      {with_ssn} ({with_ssn*100//len(customers)}%)")
    print(f"    With address:  {with_address} ({with_address*100//len(customers)}%)")
    print(f"    With co-buyer: {with_co_buyer}")
    print(f"\n  Status breakdown:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"    {status}: {count}")
    print(f"\n  Top salesreps:")
    for rep, count in list(sorted(salesrep_counts.items(), key=lambda x: -x[1]))[:5]:
        print(f"    {rep}: {count}")

    # Save local JSON
    if save_json or not load_to_firestore:
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(customers, indent=2))
        print(f"\n  Saved to: {OUTPUT_JSON}")

    # Save report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"  Report: {REPORT_PATH}")

    # Load to Firestore
    if load_to_firestore:
        print(f"\n  Loading {len(customers)} records to Firestore...")
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from database.firestore_client import THODatabase
            db = THODatabase()

            loaded = 0
            for i, customer in enumerate(customers):
                try:
                    doc_ref = db.db.collection("customers").document(customer["id"])
                    doc_ref.set(customer)
                    loaded += 1
                    if (i + 1) % 100 == 0:
                        print(f"    Loaded {i + 1}/{len(customers)}...")
                except Exception as e:
                    errors.append({"id": customer["id"], "error": str(e)})

            print(f"  Loaded {loaded}/{len(customers)} to Firestore")
            report["firestore_loaded"] = loaded
        except ImportError:
            print("  WARNING: Firestore client not available. Saved to JSON only.")
        except Exception as e:
            print(f"  ERROR loading to Firestore: {e}")

    print(f"\n{'='*60}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate FastContract customers to THO")
    parser.add_argument("--load", action="store_true", help="Load to Firestore (default: dry run)")
    parser.add_argument("--json", action="store_true", help="Save as local JSON file")
    parser.add_argument("--report", action="store_true", help="Show report only")
    args = parser.parse_args()

    migrate(load_to_firestore=args.load, save_json=args.json or not args.load)
