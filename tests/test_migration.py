"""Integration tests for FastContract customer migration."""

import json
import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent.parent / "data"
CUSTOMERS_PATH = DATA_DIR / "migrated_customers.json"
REPORT_PATH = DATA_DIR / "migration_report.json"


# These artifacts (migrated_customers.json / migration_report.json) hold raw
# customer PII and were purged from git history during the 2026-06-02 security
# cleanup. They are intentionally absent from the repo and from CI; they only
# exist on a machine that has run migrate_fastcontract.py locally. Skip rather
# than error when they are missing so CI and clean clones stay green — the data
# integrity checks still run wherever the migration output is present.
@pytest.fixture(scope="module")
def customers():
    """Load migrated customers, or skip if the local migration output is absent."""
    if not CUSTOMERS_PATH.exists():
        pytest.skip(
            "migrated_customers.json not present (purged PII; run migrate_fastcontract.py locally)"
        )
    with open(CUSTOMERS_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def report():
    """Load migration report, or skip if the local migration output is absent."""
    if not REPORT_PATH.exists():
        pytest.skip(
            "migration_report.json not present (purged PII; run migrate_fastcontract.py locally)"
        )
    with open(REPORT_PATH) as f:
        return json.load(f)


class TestMigrationData:
    """Test migrated customer data integrity."""

    def test_record_count(self, customers):
        # Sanity band, not an exact count. Floor catches data loss; ceiling
        # catches a runaway/duplicating import. Bumped after the 2026-06-04 FCD
        # differential grew the sanitized set to ~2005 (was ~1963).
        assert len(customers) >= 1900
        assert len(customers) < 2200

    def test_no_duplicate_ids(self, customers):
        ids = [c["id"] for c in customers]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_legacy_ids(self, customers):
        legacy_ids = [c["legacy_id"] for c in customers if c.get("legacy_id")]
        assert len(legacy_ids) == len(set(legacy_ids))

    def test_all_have_name(self, customers):
        for c in customers:
            assert c.get("full_name"), f"Missing name for ID {c['id']}"
            assert len(c["full_name"]) >= 2

    def test_all_have_status(self, customers):
        valid = {"ENROLLED", "LEAD", "SOLD", "CLOSED"}
        for c in customers:
            assert c["status"] in valid, f"Invalid status '{c['status']}' for {c['full_name']}"

    def test_all_have_legacy_source(self, customers):
        for c in customers:
            assert c["legacy_source"] == "fastcontract"

    def test_no_raw_ssn(self, customers):
        import re

        raw_pattern = re.compile(r"^\d{3}-\d{2}-\d{4}$")
        for c in customers:
            ssn = c.get("ssn_masked", "")
            if ssn:
                assert not raw_pattern.match(ssn), f"Raw SSN found for {c['full_name']}"
                assert ssn.startswith("***-**-"), f"Improperly masked SSN for {c['full_name']}"

    def test_valid_emails(self, customers):
        import re

        pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        for c in customers:
            if c.get("email"):
                assert pattern.match(
                    c["email"]
                ), f"Invalid email '{c['email']}' for {c['full_name']}"

    def test_valid_phones(self, customers):
        import re

        pattern = re.compile(r"^\d{3}-\d{3}-\d{4}$")
        for c in customers:
            if c.get("phone"):
                assert pattern.match(
                    c["phone"]
                ), f"Invalid phone '{c['phone']}' for {c['full_name']}"

    def test_no_test_records(self, customers):
        test_names = {"jermaine test", "test test", "test user"}
        for c in customers:
            assert (
                c["full_name"].lower() not in test_names
            ), f"Test record not filtered: {c['full_name']}"

    def test_coverage_stats(self, customers):
        """Verify data coverage meets expectations."""
        with_email = sum(1 for c in customers if c.get("email"))
        with_phone = sum(1 for c in customers if c.get("phone"))
        with_address = sum(1 for c in customers if c.get("address"))

        # Email: expect ~35%+
        assert with_email > 600, f"Email coverage too low: {with_email}"
        # Phone: expect ~62%+
        assert with_phone > 1100, f"Phone coverage too low: {with_phone}"
        # Address: expect 100%
        assert with_address == len(customers), "Not all customers have addresses"


class TestMigrationReport:
    """Test migration report accuracy."""

    def test_report_has_required_fields(self, report):
        assert "raw_records" in report
        assert "imported" in report
        assert "skipped" in report
        assert "errors" in report
        assert "stats" in report

    def test_report_counts_match(self, report, customers):
        assert report["imported"] == len(customers)
        assert report["errors"] == 0

    def test_report_stats(self, report):
        stats = report["stats"]
        assert stats["with_email"] > 0
        assert stats["with_phone"] > 0
        assert stats["with_address"] > 0


class TestCustomerSearch:
    """Test customer search functionality."""

    def test_search_by_name(self, customers):
        q = "salazar"
        results = [c for c in customers if q in c.get("full_name", "").lower()]
        assert len(results) > 0, f"No results for '{q}'"

    def test_search_by_phone(self, customers):
        q = "936"
        results = [c for c in customers if q in (c.get("phone") or "").replace("-", "")]
        assert len(results) > 0

    def test_search_by_legacy_id(self, customers):
        # Pick a known ID
        sample = customers[0]
        lid = sample["legacy_id"]
        results = [c for c in customers if c.get("legacy_id") == lid]
        assert len(results) == 1

    def test_search_by_salesrep(self, customers):
        results = [c for c in customers if "squyres" in (c.get("salesrep") or "").lower()]
        assert len(results) > 100  # Adriana Squyres is a top rep
