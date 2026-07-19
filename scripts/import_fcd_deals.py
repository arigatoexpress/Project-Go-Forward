#!/usr/bin/env python3
"""
Import FCD (FastContractDocs) historical records into Firestore deals collection.

Reads full_migration_export.csv (1,994 records, 42 columns) and maps FCD columns
to Deal model fields, writing to the `deals` collection in Firestore.

Usage:
    python3 scripts/import_fcd_deals.py                    # dry-run (default)
    python3 scripts/import_fcd_deals.py --commit           # actually write to Firestore
    python3 scripts/import_fcd_deals.py --commit --limit=50  # import first 50 only
"""

import argparse
import csv
import os
import sys
import uuid
from datetime import UTC, datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.firestore_timeouts import firestore_long_timeout  # noqa: E402

# ─── FCD Status → Deal Status mapping ───
STATUS_MAP = {
    "Contract": "complete",  # Old contracts → completed
    "Pending": "archived",  # Old pending → archived (stale leads)
    "Approved": "complete",  # Old approved → completed
    "Archived": "archived",  # Already archived
}

# Marital status normalization
MARITAL_MAP = {
    "M": "Married",
    "S": "Single",
    "D": "Divorced",
    "W": "Widowed",
    "N": None,  # Not specified
    "U": None,  # Unknown
    "": None,
}

# Own/Rent normalization
OWN_RENT_MAP = {
    "O": "Own",
    "R": "Rent",
    "": None,
}

SENSITIVE_LOG_TOKENS = (
    "app_id",
    "email",
    "phone",
    "ssn",
    "name",
    "address",
    "city",
    "zip",
    "reference",
    "notes",
    "employer",
    "occupation",
)


def redact_for_log(key: str, value):
    """Avoid printing customer PII during dry-run previews."""
    if value is None:
        return None
    key_lower = key.lower()
    if any(token in key_lower for token in SENSITIVE_LOG_TOKENS):
        return "<redacted>"
    return value


def parse_ssn(raw: str) -> str | None:
    """Format raw SSN digits into XXX-XX-XXXX format."""
    if not raw or len(raw) < 9:
        return None
    digits = raw.strip().replace("-", "").replace(" ", "")
    if len(digits) == 9 and digits.isdigit():
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return raw.strip() if raw.strip() else None


def parse_phone(raw: str) -> str | None:
    """Clean up phone number."""
    if not raw or not raw.strip():
        return None
    phone = raw.strip().replace("(", "").replace(")", "").replace(" ", "").replace(".", "-")
    # If 10 digits, format nicely
    digits = phone.replace("-", "")
    if len(digits) == 10 and digits.isdigit():
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone if phone else None


def parse_appid_date(app_id: str) -> datetime | None:
    """Extract date from FCD AppID format: YYMMDDHHMMSS → datetime."""
    if not app_id or len(app_id) < 6:
        return None
    try:
        # Format: 210602034358 → 2021-06-02 03:43:58
        yy = int(app_id[:2])
        mm = int(app_id[2:4])
        dd = int(app_id[4:6])
        year = 2000 + yy if yy < 50 else 1900 + yy
        # Clamp invalid months/days
        mm = max(1, min(12, mm))
        dd = max(1, min(28, dd))  # safe default
        return datetime(year, mm, dd)
    except (ValueError, IndexError):
        return None


def row_to_deal(row: dict) -> dict | None:
    """Convert a CSV row to a Firestore deal document."""
    first = (row.get("Buyer_First_Name") or "").strip()
    last = (row.get("Buyer_Last_Name") or "").strip()

    # Skip rows without any name
    if not first and not last:
        return None

    # Parse creation date from AppID
    created = parse_appid_date(row.get("AppID", ""))
    now = datetime.now(UTC)

    deal = {
        "id": str(uuid.uuid4()),
        "source": "fcd_import",  # Tag so we can identify imported records
        "fcd_app_id": row.get("AppID", ""),
        # Status
        "status": STATUS_MAP.get(row.get("ChangeAppStatus", ""), "archived"),
        "salesrep": row.get("Salesrep", "").strip() or None,
        # Buyer
        "buyer_first_name": first or None,
        "buyer_last_name": last or None,
        "buyer_phone": parse_phone(row.get("Phone1", "")),
        "buyer_email": row.get("Buyer_Email", "").strip() or None,
        "buyer_ssn": parse_ssn(row.get("SSN1", "")),
        "buyer_marital_status": MARITAL_MAP.get(row.get("Buyer_Marital_Status", ""), None),
        # Co-Buyer
        "co_buyer_first_name": row.get("CoBuyer_First_Name", "").strip() or None,
        "co_buyer_last_name": row.get("CoBuyer_Last_Name", "").strip() or None,
        "co_buyer_ssn": parse_ssn(row.get("SSN2", "")),
        "co_buyer_marital_status": MARITAL_MAP.get(row.get("CoBuyer_Marital_Status", ""), None),
        "co_buyer_work_phone": parse_phone(row.get("CoBuyer_Work_Phone1", "")),
        # Employment (Buyer)
        "employer_name": row.get("Employer_Name", "").strip() or None,
        "occupation": row.get("Occupation", "").strip() or None,
        "occupation_length": row.get("Occupation_Length", "").strip() or None,
        "work_phone": parse_phone(row.get("Work_Phone1", "")),
        "self_employed": row.get("Buyer_Self_Emp", "").strip().upper() in ("Y", "YES", "TRUE", "1"),
        "previous_employer": row.get("Previous_Employer_Name", "").strip() or None,
        "previous_occupation": row.get("Previous_Occupation", "").strip() or None,
        # Employment (Co-Buyer)
        "co_buyer_employer": row.get("CoBuyer_Employer_Name", "").strip() or None,
        "co_buyer_occupation": row.get("CoBuyer_Occupation", "").strip() or None,
        "co_buyer_occupation_length": row.get("CoBuyer_Occupation_Length", "").strip() or None,
        "co_buyer_self_employed": row.get("CoBuyer_Self_Emp", "").strip().upper()
        in ("Y", "YES", "TRUE", "1"),
        # Mailing Address
        "mailing_address": row.get("Mailing_Address", "").strip() or None,
        "mailing_city": row.get("Mailing_City", "").strip() or None,
        "mailing_state": row.get("Mailing_State", "").strip() or "TX",
        "mailing_zip": row.get("Mailing_Zip", "").strip() or None,
        "mailing_length": row.get("Mailing_Length", "").strip() or None,
        "mailing_own_rent": OWN_RENT_MAP.get(row.get("Mailing_Own", ""), None),
        # References
        "reference1_name": row.get("Reference1_Name", "").strip() or None,
        "reference1_phone": parse_phone(row.get("Reference1_Phone", "")),
        "reference2_name": row.get("Reference2_Name", "").strip() or None,
        "reference2_phone": parse_phone(row.get("Reference2_Phone", "")),
        # Notes
        "notes": row.get("Notes", "").strip() or None,
        # Home info — FCD didn't store home details in the export, those were in separate tables
        # These will be empty; employees can link inventory later
        "is_new": True,
        # Timestamps
        "created_at": created.isoformat() if created else now.isoformat(),
        "updated_at": now.isoformat(),
    }

    # Strip None values to keep Firestore docs clean
    return {k: v for k, v in deal.items() if v is not None}


def main():
    parser = argparse.ArgumentParser(description="Import FCD records into Firestore deals")
    parser.add_argument(
        "--commit", action="store_true", help="Actually write to Firestore (default is dry-run)"
    )
    parser.add_argument("--limit", type=int, default=0, help="Max records to import (0 = all)")
    parser.add_argument("--csv-path", default=None, help="Path to CSV file")
    args = parser.parse_args()

    # Find CSV
    csv_path = args.csv_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "full_migration_export.csv",
    )

    if not os.path.exists(csv_path):
        print(f"ERROR: CSV not found at {csv_path}")
        sys.exit(1)

    print(f"Reading: {csv_path}")
    print(f"Mode: {'COMMIT (writing to Firestore)' if args.commit else 'DRY RUN (no writes)'}")
    if args.limit:
        print(f"Limit: {args.limit} records")
    print()

    # Read CSV
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total CSV rows: {len(rows)}")

    # Convert to deals
    deals = []
    skipped = 0
    for row in rows:
        deal = row_to_deal(row)
        if deal:
            deals.append(deal)
        else:
            skipped += 1

    print(f"Valid deals: {len(deals)}")
    print(f"Skipped (no name): {skipped}")

    if args.limit:
        deals = deals[: args.limit]
        print(f"After limit: {len(deals)}")

    # Status breakdown
    status_counts = {}
    for d in deals:
        s = d.get("status", "?")
        status_counts[s] = status_counts.get(s, 0) + 1
    print("\nStatus distribution:")
    for s, c in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}")

    # Sample
    print("\nSample deal (first, redacted):")
    if deals:
        sample = deals[0]
        for k, v in sample.items():
            print(f"  {k}: {redact_for_log(k, v)}")

    if not args.commit:
        print("\n--- DRY RUN COMPLETE ---")
        print("Run with --commit to actually write to Firestore.")
        return

    # ─── Write to Firestore ───
    print("\nConnecting to Firestore...")

    try:
        from google.cloud import firestore
    except ImportError:
        print(
            "ERROR: google-cloud-firestore not installed. Run: pip install google-cloud-firestore"
        )
        sys.exit(1)

    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
    db = firestore.Client(project=project_id)
    collection = db.collection("deals")

    print(f"Writing {len(deals)} deals to Firestore project '{project_id}'...")

    # Batch write (500 per batch — Firestore limit)
    batch_size = 450  # Leave room under the 500 limit
    written = 0
    errors = 0

    for i in range(0, len(deals), batch_size):
        batch = db.batch()
        chunk = deals[i : i + batch_size]

        for deal in chunk:
            doc_ref = collection.document(deal["id"])
            batch.set(doc_ref, deal)

        try:
            batch.commit(timeout=firestore_long_timeout())
            written += len(chunk)
            print(f"  Batch {i // batch_size + 1}: wrote {len(chunk)} deals (total: {written})")
        except Exception as e:
            errors += len(chunk)
            print(f"  Batch {i // batch_size + 1}: ERROR - {e}")

    print("\n=== IMPORT COMPLETE ===")
    print(f"Written: {written}")
    print(f"Errors: {errors}")
    print(f"Total: {written + errors}")


if __name__ == "__main__":
    main()
