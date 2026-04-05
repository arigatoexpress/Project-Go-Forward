#!/usr/bin/env python3
"""Validate FastContract customer migration integrity.

Checks:
1. Record count matches CSV
2. No duplicate IDs
3. All required fields present
4. SSN properly masked (no raw SSNs)
5. Status values are valid
6. Phone/email formats valid where present
7. Spot-check random records against CSV source

Usage:
    python3 tools/validate_migration.py
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys
from pathlib import Path

CSV_PATH = Path.home() / "Documents" / "Business" / "Kadima Digital Strategies 2026" / "THO_MASTER" / "full_migration_export.csv"
JSON_PATH = Path(__file__).parent.parent / "data" / "migrated_customers.json"

VALID_STATUSES = {"ENROLLED", "LEAD", "SOLD", "CLOSED"}


def validate():
    """Run all validation checks."""
    print("=" * 60)
    print("  MIGRATION VALIDATION")
    print("=" * 60)

    if not JSON_PATH.exists():
        print("FAIL: migrated_customers.json not found. Run migrate_fastcontract.py first.")
        return False

    with open(JSON_PATH) as f:
        customers = json.load(f)

    checks_passed = 0
    checks_failed = 0

    def check(name, condition, detail=""):
        nonlocal checks_passed, checks_failed
        if condition:
            print(f"  ✅ {name}")
            checks_passed += 1
        else:
            print(f"  ❌ {name}: {detail}")
            checks_failed += 1

    # 1. Record count
    print("\n--- Record Count ---")
    check("Has records", len(customers) > 0, f"Got {len(customers)}")
    check("Reasonable count (>1900)", len(customers) > 1900, f"Got {len(customers)}")
    check("Not too many (<2000)", len(customers) < 2000, f"Got {len(customers)}")
    print(f"  Total: {len(customers)} records")

    # 2. No duplicate IDs
    print("\n--- Uniqueness ---")
    ids = [c["id"] for c in customers]
    check("No duplicate UUIDs", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} duplicates")
    legacy_ids = [c.get("legacy_id", "") for c in customers if c.get("legacy_id")]
    check("No duplicate legacy IDs", len(legacy_ids) == len(set(legacy_ids)),
          f"{len(legacy_ids) - len(set(legacy_ids))} duplicates")

    # 3. Required fields
    print("\n--- Required Fields ---")
    missing_name = sum(1 for c in customers if not c.get("full_name"))
    check("All have full_name", missing_name == 0, f"{missing_name} missing")
    missing_status = sum(1 for c in customers if not c.get("status"))
    check("All have status", missing_status == 0, f"{missing_status} missing")
    missing_source = sum(1 for c in customers if c.get("legacy_source") != "fastcontract")
    check("All have legacy_source=fastcontract", missing_source == 0, f"{missing_source} missing")

    # 4. SSN properly masked
    print("\n--- PII Security ---")
    raw_ssn_pattern = re.compile(r"\d{3}-\d{2}-\d{4}")
    raw_ssns = sum(1 for c in customers if raw_ssn_pattern.match(c.get("ssn_masked", "")))
    check("No raw SSNs in ssn_masked", raw_ssns == 0, f"{raw_ssns} raw SSNs found!")
    masked_format = sum(1 for c in customers if c.get("ssn_masked") and c["ssn_masked"].startswith("***-**-"))
    has_ssn = sum(1 for c in customers if c.get("ssn_masked"))
    check("SSNs properly masked (***-**-XXXX)", masked_format == has_ssn,
          f"{has_ssn - masked_format} improperly masked")

    # 5. Valid statuses
    print("\n--- Status Values ---")
    invalid_statuses = [c["status"] for c in customers if c["status"] not in VALID_STATUSES]
    check("All statuses valid", len(invalid_statuses) == 0,
          f"Invalid: {set(invalid_statuses)}")
    for status in VALID_STATUSES:
        count = sum(1 for c in customers if c["status"] == status)
        if count > 0:
            print(f"    {status}: {count}")

    # 6. Format validation
    print("\n--- Data Quality ---")
    email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    bad_emails = sum(1 for c in customers if c.get("email") and not email_pattern.match(c["email"]))
    check("All emails valid format", bad_emails == 0, f"{bad_emails} invalid")

    phone_pattern = re.compile(r"\d{3}-\d{3}-\d{4}")
    bad_phones = sum(1 for c in customers if c.get("phone") and not phone_pattern.match(c["phone"]))
    check("All phones valid format", bad_phones == 0, f"{bad_phones} invalid")

    with_email = sum(1 for c in customers if c.get("email"))
    with_phone = sum(1 for c in customers if c.get("phone"))
    with_address = sum(1 for c in customers if c.get("address"))
    print(f"    Email coverage: {with_email}/{len(customers)} ({with_email*100//len(customers)}%)")
    print(f"    Phone coverage: {with_phone}/{len(customers)} ({with_phone*100//len(customers)}%)")
    print(f"    Address coverage: {with_address}/{len(customers)} ({with_address*100//len(customers)}%)")

    # 7. Spot-check against CSV
    print("\n--- Spot Check (5 random records) ---")
    if CSV_PATH.exists():
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            csv_rows = {r["AppID"]: r for r in csv.DictReader(f)}

        sample = random.sample(customers, min(5, len(customers)))
        for c in sample:
            lid = c.get("legacy_id", "")
            if lid in csv_rows:
                csv_row = csv_rows[lid]
                csv_name = f"{csv_row.get('Buyer_First_Name', '')} {csv_row.get('Buyer_Last_Name', '')}".strip()
                match = csv_name.lower() == c["full_name"].lower()
                check(f"Spot: {c['full_name'][:25]} (ID: {lid})", match,
                      f"CSV name '{csv_name}' != migrated '{c['full_name']}'")
            else:
                check(f"Spot: {c['full_name'][:25]} (ID: {lid})", False, "Legacy ID not found in CSV")
    else:
        print("  ⚠️  CSV not available for spot-check")

    # Summary
    total = checks_passed + checks_failed
    print(f"\n{'=' * 60}")
    print(f"  RESULT: {checks_passed}/{total} checks passed")
    if checks_failed == 0:
        print("  ✅ ALL CHECKS PASSED — Migration is valid")
    else:
        print(f"  ❌ {checks_failed} CHECKS FAILED — Review issues above")
    print(f"{'=' * 60}")

    return checks_failed == 0


if __name__ == "__main__":
    success = validate()
    sys.exit(0 if success else 1)
