"""Coverage mop-up for the c12-c campaign.

Targets (measured from baseline, not the stale 3.12 ranking):
- tools.house_orders_sanitizer (115 missing lines at baseline)
- tools.convert_inventory (never imported by any test at baseline)
- seo_routes (37 missing lines at baseline)
- database.models (45 missing lines at baseline)

All tests are hermetic: no Firestore emulator, no network, tmp_path fixtures.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent))

import tools.house_orders_sanitizer as sanitizer  # noqa: E402
from database.models import (  # noqa: E402
    Customer,
    Deal,
    GoogleAdsAccessEvidenceRecord,
    GoogleAdsDeploymentRecord,
    GoogleAdsOperationKeyRecord,
    GoogleAdsOwnerStepUpEvidenceRecord,
    GoogleAdsPausedCreateApprovalProofRecord,
    GoogleAdsPausedCreateOutboxRecord,
    GoogleAdsStepUpCapsRecord,
    Inventory,
)
from scripts.google_ads_access_evidence import (  # noqa: E402
    AccessCheckKey,
    AccessEvidenceStatus,
    build_access_evidence,
    evidence_payload,
)
from scripts.google_ads_launch_draft import contract_sha256  # noqa: E402
from scripts.google_ads_paused_worker import deployment_id  # noqa: E402

SANITIZER_PATH = Path(sanitizer.__file__).resolve()
CONVERT_INVENTORY_PATH = SANITIZER_PATH.parent / "convert_inventory.py"


def _row(data: dict, row_number: int = 2) -> sanitizer.SanitizedHouseOrderRow:
    return sanitizer.SanitizedHouseOrderRow(
        row_number=row_number, data=data, source_columns={}, warnings=[]
    )


# ─── tools.house_orders_sanitizer: coercion + column helpers ────────────────


def test_sanitizer_is_missing_tolerates_array_values():
    # pd.isna([1, 2]) returns an array whose truthiness raises ValueError;
    # the guard must treat such values as "not missing" instead of crashing.
    assert sanitizer._is_missing([1, 2]) is False
    assert sanitizer._is_missing(float("nan")) is True


def test_sanitizer_float_and_int_parsers_return_none_on_garbage():
    assert sanitizer._to_float("not-a-number") is None
    assert sanitizer._to_int("not-a-number") is None
    assert sanitizer._to_float("$1,234.50") == 1234.5
    assert sanitizer._to_int("1,234") == 1234


def test_sanitizer_to_date_iso_covers_all_branches():
    assert sanitizer._to_date_iso(None) is None
    assert sanitizer._to_date_iso(float("nan")) is None
    stamp = datetime(2026, 3, 4, 15, 30)
    assert sanitizer._to_date_iso(stamp) == "2026-03-04"
    assert sanitizer._to_date_iso(date(2026, 3, 4)) == "2026-03-04"
    assert sanitizer._to_date_iso("2026-03-04") == "2026-03-04"
    assert sanitizer._to_date_iso("not a date") is None


def test_sanitizer_parse_dimensions_and_dimension_pair_fallback():
    assert sanitizer._parse_dimensions(None) == (None, None)
    assert sanitizer._parse_dimensions("32 x 76") == (32, 76)
    assert sanitizer._parse_dimensions("32by76") == (32, 76)
    assert sanitizer._dimension_pair(None, width="28", length="56") == (28, 56)
    assert sanitizer._dimension_pair(None, width=None, length="56") == (None, None)


def test_sanitizer_same_identifier_requires_both_sides():
    assert sanitizer._same_identifier("", "ABC123") is False
    assert sanitizer._same_identifier("ABC123", None) is False
    assert sanitizer._same_identifier("abc123", "ABC123") is True


def test_sanitizer_model_tokens_preserves_compact_handles():
    tokens = sanitizer._model_tokens("Pt 78 special")
    assert "pt78" in tokens
    # compact product handle without the alpha-number adjacency
    tokens = sanitizer._model_tokens("S167232B home")
    assert "s167232b" in tokens


def test_sanitizer_column_maps_buckets_unknown_columns_as_ignored():
    mapped, sensitive, ignored = sanitizer._column_maps(
        ["Inventory ID", "MSRP", "Widget Color", "Buyer Phone"]
    )
    assert mapped["inventory_id"] == "Inventory ID"
    assert mapped["msrp"] == "MSRP"
    assert sensitive == ["Buyer Phone"]
    assert ignored == ["Widget Color"]


def test_sanitizer_values_equal_both_none():
    assert sanitizer._values_equal(None, None) is True
    assert sanitizer._values_equal("1", 1) is True
    assert sanitizer._values_equal("1", "2") is False


# ─── tools.house_orders_sanitizer: parse row skips ──────────────────────────


def test_sanitizer_parse_skips_empty_and_identifierless_rows():
    df = pd.DataFrame(
        {
            "Inventory ID": [None, None, "100"],
            "Serial #": [None, None, "SN100"],
            "MSRP": [None, 50000, 60000],
            "Model": [None, None, "Bourbon"],
        }
    )
    rows, metadata = sanitizer.parse_house_orders_dataframe(df)
    # row 0 has no data at all; row 1 has data but no id/serial/model
    assert len(rows) == 1
    assert rows[0].data["inventory_id"] == "100"
    assert metadata["sanitized_rows"] == 1


# ─── tools.house_orders_sanitizer: patch plan branches ──────────────────────


class _FakeInventoryDB:
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


def test_sanitizer_patch_plan_covers_id_match_no_changes_and_to_dict():
    row_changed = _row(
        {"inventory_id": "100", "model_name": "Bourbon", "msrp": 55000.0}, row_number=2
    )
    row_same = _row(
        {"inventory_id": "200", "model_name": "Creole", "msrp": 50000.0}, row_number=3
    )
    db = _FakeInventoryDB(
        existing_by_id={
            "100": {"id": "100", "model_name": "Bourbon", "msrp": "50000"},
            "200": {"id": "200", "model_name": "Creole", "msrp": 50000.0},
        }
    )
    plans = sanitizer.build_firestore_patch_plan([row_changed, row_same], db)

    matched = plans[0]
    assert matched.match_status == "matched"
    assert matched.match_field == "inventory_id"
    assert matched.patch == {"msrp": 55000.0}
    as_dict = matched.to_dict()
    assert as_dict["inventory_id"] == "100"
    assert as_dict["patch"] == {"msrp": 55000.0}

    unchanged = plans[1]
    assert unchanged.match_status == "no_changes"
    assert unchanged.skipped_reason == "Existing inventory already has the sanitized values."
    assert unchanged.to_dict()["match_status"] == "no_changes"


def test_sanitizer_apply_plan_counts_failures_without_raising():
    plan_ok = sanitizer.InventoryPatchPlan(
        row_number=2,
        match_status="matched",
        inventory_id="100",
        match_field="inventory_id",
        match_value="100",
        model_name="Bourbon",
        patch={"msrp": 1.0},
    )
    plan_fail = sanitizer.InventoryPatchPlan(
        row_number=3,
        match_status="matched",
        inventory_id="200",
        match_field="inventory_id",
        match_value="200",
        model_name="Creole",
        patch={"msrp": 2.0},
    )
    plan_skip = sanitizer.InventoryPatchPlan(
        row_number=4,
        match_status="missing_match",
        inventory_id=None,
        match_field=None,
        match_value=None,
        model_name=None,
        patch={},
        skipped_reason="nope",
    )

    class FlakyDB:
        def __init__(self):
            self.calls = []

        def update_inventory(self, inventory_id, patch):
            self.calls.append(inventory_id)
            raise RuntimeError("emulator down")

    counts = sanitizer.apply_firestore_patch_plan([plan_ok, plan_fail, plan_skip], FlakyDB())
    assert counts == {"updated": 0, "skipped": 1, "failed": 2}


# ─── tools.house_orders_sanitizer: reconciliation scoring branches ──────────


def test_sanitizer_score_candidate_inventory_id_exact_match():
    row = _row({"inventory_id": "100", "model_name": "Bourbon"})
    candidate = sanitizer._score_candidate(row, {"id": "100", "model_name": "Other"})
    assert candidate.score == 120
    assert "inventory_id exact" in candidate.reasons
    # also matches on the inventory_id key (not just id)
    candidate = sanitizer._score_candidate(row, {"inventory_id": "100"})
    assert "inventory_id exact" in candidate.reasons


def test_sanitizer_score_candidate_model_exact_and_similarity():
    row = _row({"model_name": "Bourbon 32x76"})
    exact = sanitizer._score_candidate(row, {"model_name": "Bourbon 32x76"})
    assert "model exact" in exact.reasons

    similar = sanitizer._score_candidate(row, {"model_name": "Bourbone 32x76"})
    reasons = " ".join(similar.reasons)
    assert "model similarity" in reasons


def test_sanitizer_score_candidate_dimension_branches():
    base = {"width": 32, "length": 76}
    row = _row({"model_name": "Bourbon", "width": 32, "length": 76})
    # exact dims
    got = sanitizer._score_candidate(row, {**base, "model_name": "Other"})
    assert any("dimensions match" in r for r in got.reasons)
    # reversed dims
    got = sanitizer._score_candidate(
        _row({"model_name": "Bourbon", "width": 76, "length": 32}),
        {**base, "model_name": "Other"},
    )
    assert any("dimensions reversed match" in r for r in got.reasons)
    # width within 2, same length
    got = sanitizer._score_candidate(
        _row({"model_name": "Bourbon", "width": 30, "length": 76}),
        {**base, "model_name": "Other"},
    )
    assert any("dimensions close" in r for r in got.reasons)
    # one dimension matches
    got = sanitizer._score_candidate(
        _row({"model_name": "Bourbon", "width": 16, "length": 76}),
        {**base, "model_name": "Other"},
    )
    assert any("one dimension matches" in r for r in got.reasons)


def test_sanitizer_price_match_reason_branches():
    assert sanitizer._price_match_reason(None, 100) is None
    assert sanitizer._price_match_reason(100, None) is None
    assert sanitizer._price_match_reason(0, 100) is None
    assert sanitizer._price_match_reason(100, -5) is None
    assert sanitizer._price_match_reason(100, 101) == (20, "price within 2%")
    assert sanitizer._price_match_reason(100, 105) == (10, "price within 10%")
    assert sanitizer._price_match_reason(100, 130) is None


def test_sanitizer_reconcile_notes_for_no_match_and_close_race():
    no_match = sanitizer._reconcile_one_row(
        _row({"model_name": "Zzz Nothing Alike"}), [{"model_name": "Totally Different"}], 3
    )
    assert no_match.confidence == "no_match"
    assert no_match.notes == ["No plausible inventory candidate found."]

    # two candidates whose model tokens tie -> close race note
    inventory = [
        {"id": "a", "model_name": "Bourbon 3276"},
        {"id": "b", "model_name": "Bourbon 3276"},
    ]
    record = sanitizer._reconcile_one_row(
        _row({"model_name": "Bourbon 3276"}), inventory, 3
    )
    assert record.confidence == "review"
    assert any("close" in note for note in record.notes)


# ─── tools.house_orders_sanitizer: xlsx IO + main() ─────────────────────────


def _write_house_orders_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Texas Home Outlet — House Orders"])  # title/preamble row
    sheet.append(["Inventory ID", "Serial #", "Model", "MSRP", "Buyer Phone"])
    sheet.append(["100", "SN100", "Bourbon", 55000, "555-1234"])
    sheet.append(["200", "SN200", "Creole", 60000, "555-5678"])
    workbook.save(path)


def test_sanitizer_read_xlsx_auto_and_explicit_header(tmp_path):
    xlsx = tmp_path / "House Orders.xlsx"
    _write_house_orders_xlsx(xlsx)

    auto = sanitizer.read_house_orders_xlsx(xlsx, header_row="auto")
    assert auto.attrs["house_orders_header_row"] == 1
    assert list(auto.columns) == ["Inventory ID", "Serial #", "Model", "MSRP", "Buyer Phone"]

    explicit = sanitizer.read_house_orders_xlsx(xlsx, header_row=1)
    assert explicit.attrs["house_orders_header_row"] == 1


def test_sanitizer_write_or_print_output_and_stdout(tmp_path, capsys):
    payload = {"b": 1, "a": 2}
    out = tmp_path / "nested" / "report.json"
    sanitizer._write_or_print(payload, out)
    assert json.loads(out.read_text()) == payload
    assert "Wrote" in capsys.readouterr().out

    sanitizer._write_or_print(payload, None)
    printed = json.loads(capsys.readouterr().out)
    assert printed == payload


def _run_sanitizer_main(monkeypatch, argv, fake_db=None):
    """Run house_orders_sanitizer.py in-process as __main__ with faked DB."""
    fake_db = fake_db or _FakeInventoryDB()
    module = SimpleNamespace(THODatabase=lambda: fake_db)
    monkeypatch.setitem(sys.modules, "database.firestore_client", module)
    # belt-and-suspenders: cover the parent-package attribute path too
    import database

    monkeypatch.setattr(database, "firestore_client", module, raising=False)
    monkeypatch.setattr(sys, "argv", ["house_orders_sanitizer.py", *argv])
    runpy.run_path(str(SANITIZER_PATH), run_name="__main__")
    return fake_db


def test_sanitizer_main_dry_run_writes_report(tmp_path, monkeypatch, capsys):
    xlsx = tmp_path / "House Orders.xlsx"
    _write_house_orders_xlsx(xlsx)
    out = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        _run_sanitizer_main(
            monkeypatch, ["--xlsx", str(xlsx), "--dry-run", "--output", str(out)]
        )
    assert exc.value.code == 0
    report = json.loads(out.read_text())
    assert report["success"] is True
    assert report["header_row"] == 1
    assert report["sensitive_columns_dropped"] == ["Buyer Phone"]
    assert len(report["records"]) == 2


def test_sanitizer_main_sheet_by_name(tmp_path, monkeypatch):
    xlsx = tmp_path / "House Orders.xlsx"
    _write_house_orders_xlsx(xlsx)
    out = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        _run_sanitizer_main(
            monkeypatch,
            ["--xlsx", str(xlsx), "--sheet", "Sheet", "--header-row", "1", "--dry-run", "--output", str(out)],
        )
    assert exc.value.code == 0
    assert json.loads(out.read_text())["success"] is True


def test_sanitizer_main_dry_run_prints_json_without_output(tmp_path, monkeypatch, capsys):
    xlsx = tmp_path / "House Orders.xlsx"
    _write_house_orders_xlsx(xlsx)
    with pytest.raises(SystemExit) as exc:
        _run_sanitizer_main(monkeypatch, ["--xlsx", str(xlsx), "--dry-run"])
    assert exc.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["success"] is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--xlsx", "placeholder.xlsx"],  # neither --dry-run nor --apply
        ["--xlsx", "placeholder.xlsx", "--dry-run", "--apply"],
        ["--xlsx", "placeholder.xlsx", "--dry-run", "--reconciliation-csv", "x.csv"],
        ["--xlsx", "does-not-exist.xlsx", "--dry-run"],
    ],
)
def test_sanitizer_main_parser_errors_exit_two(argv, monkeypatch, tmp_path):
    argv = [str(tmp_path / a) if a == "placeholder.xlsx" else a for a in argv]
    with pytest.raises(SystemExit) as exc:
        _run_sanitizer_main(monkeypatch, argv)
    assert exc.value.code == 2


def test_sanitizer_main_compare_firestore_and_reconcile_csv(tmp_path, monkeypatch):
    xlsx = tmp_path / "House Orders.xlsx"
    _write_house_orders_xlsx(xlsx)
    out = tmp_path / "report.json"
    csv_out = tmp_path / "reconcile.csv"
    db = _FakeInventoryDB(
        existing_by_serial={"SN100": {"id": "inv-100", "model_name": "Bourbon"}},
        inventory=[{"id": "inv-100", "model_name": "Bourbon", "serial_number": "SN100"}],
    )
    with pytest.raises(SystemExit) as exc:
        _run_sanitizer_main(
            monkeypatch,
            [
                "--xlsx",
                str(xlsx),
                "--dry-run",
                "--compare-firestore",
                "--reconcile-inventory",
                "--reconciliation-csv",
                str(csv_out),
                "--output",
                str(out),
            ],
            fake_db=db,
        )
    assert exc.value.code == 0
    report = json.loads(out.read_text())
    assert report["patch_summary"]["matched"] == 1
    assert report["patch_summary"]["missing_match"] == 1
    assert report["reconciliation"]["summary"]["total_rows"] == 2
    assert csv_out.exists()


def test_sanitizer_main_apply_returns_one_when_updates_fail(tmp_path, monkeypatch):
    xlsx = tmp_path / "House Orders.xlsx"
    _write_house_orders_xlsx(xlsx)
    out = tmp_path / "report.json"

    class FailingDB(_FakeInventoryDB):
        def __init__(self):
            super().__init__(
                existing_by_serial={"SN100": {"id": "inv-100", "model_name": "Bourbon"}}
            )

        def update_inventory(self, inventory_id, patch):
            raise RuntimeError("emulator down")

    with pytest.raises(SystemExit) as exc:
        _run_sanitizer_main(
            monkeypatch, ["--xlsx", str(xlsx), "--apply", "--output", str(out)],
            fake_db=FailingDB(),
        )
    assert exc.value.code == 1
    report = json.loads(out.read_text())
    assert report["apply_summary"] == {"updated": 0, "skipped": 1, "failed": 1}


# ─── tools.convert_inventory ────────────────────────────────────────────────


def _convert_inventory_df() -> pd.DataFrame:
    rows = [
        # serial NaN -> skipped
        {"Serial #": float("nan"), "Model": "Bourbon 32x76 4/2", "MSRP": 80000},
        # repeated header row -> skipped
        {"Serial #": "Serial #", "Model": "Bourbon", "MSRP": 80000},
        # section marker row -> skipped
        {"Serial #": "Section 1 Approval", "Model": "Bourbon", "MSRP": 80000},
        # no digits -> skipped
        {"Serial #": "ABC", "Model": "Bourbon", "MSRP": 80000},
        # empty model -> skipped
        {"Serial #": "SN001", "Model": "", "MSRP": 80000},
        # double wide with dims + beds/baths + MSRP string garbage
        {
            "Serial #": "SN100",
            "Model": "Bourbon 32x76 4/2",
            "MSRP": "not-a-number",
            "Invoice Amount": "Invoice Amount",
            "Manufacturing Plant": "Champion LA",
        },
        # single wide, low price tier, NaN msrp -> 0, NaN plant -> Unknown,
        # invoice amount numeric
        {
            "Serial #": "SN200",
            "Model": "Creole 16x80 3/2",
            "MSRP": float("nan"),
            "Invoice Amount": 40000,
            "Manufacturing Plant": float("nan"),
        },
        # mid tiers + no dim match -> Unknown classification
        {"Serial #": "SN300", "Model": "Durango", "MSRP": 60000},
        {"Serial #": "SN400", "Model": "Durango", "MSRP": 90000},
        {"Serial #": "SN500", "Model": "Durango", "MSRP": 120000},
        {"Serial #": "SN600", "Model": "Durango", "MSRP": 200000},
        # garbage invoice amount -> None via the ValueError branch
        {
            "Serial #": "SN700",
            "Model": "Durango 28x40",
            "MSRP": 80000,
            "Invoice Amount": "not-a-number",
            "Manufacturing Plant": "Champion LA",
        },
    ]
    return pd.DataFrame(rows)


def _patch_convert_inventory_os(monkeypatch, tmp_path):
    """Point convert_inventory's os.path.dirname at tmp_path without touching
    the real os module (pandas keeps working)."""
    import tools.convert_inventory as ci

    fake_os = SimpleNamespace(
        path=SimpleNamespace(
            dirname=lambda _p: str(tmp_path),
            join=os.path.join,
            exists=os.path.exists,
        )
    )
    monkeypatch.setattr(ci, "os", fake_os)
    return ci


def test_convert_inventory_missing_excel_file_prints_and_returns(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").mkdir()
    ci = _patch_convert_inventory_os(monkeypatch, tmp_path)
    ci.convert_excel_to_json()
    assert "Excel file not found!" in capsys.readouterr().out


def _write_convert_inventory_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Everybody Else"
    sheet.append(["Texas Home Outlet — House Orders"])  # preamble row (header=1)
    sheet.append(["Serial #", "Model", "MSRP", "Invoice Amount", "Manufacturing Plant"])
    for row in _convert_inventory_df().itertuples(index=False):
        values = [None if pd.isna(v) else v for v in row]
        sheet.append(values)
    workbook.save(path)


def test_convert_inventory_end_to_end(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_convert_inventory_xlsx(data_dir / "House Orders.xlsx")
    ci = _patch_convert_inventory_os(monkeypatch, tmp_path)

    ci.convert_excel_to_json()

    inventory = json.loads((data_dir / "inventory.json").read_text())
    by_id = {item["serial_number"]: item for item in inventory}
    assert set(by_id) == {
        "SN100",
        "SN200",
        "SN300",
        "SN400",
        "SN500",
        "SN600",
        "SN700",
    }

    double_wide = by_id["SN100"]
    assert double_wide["classification"] == "Double Wide"
    assert double_wide["specs"] == {"beds": 4, "baths": 2, "sq_ft": 32 * 76}
    assert double_wide["pricing"]["price_value"] == 0
    assert double_wide["pricing"]["display_price"] == "Call for Price"
    assert double_wide["pricing"]["price_tier"] == "Under $50k"
    assert double_wide["pricing"]["invoice_amount"] is None
    assert double_wide["manufacturer"] == "Champion LA"

    single_wide = by_id["SN200"]
    assert single_wide["classification"] == "Single Wide"
    assert single_wide["manufacturer"] == "Unknown"
    assert single_wide["pricing"]["invoice_amount"] == 40000

    assert by_id["SN300"]["pricing"]["price_tier"] == "$50k-$75k"
    assert by_id["SN400"]["pricing"]["price_tier"] == "$75k-$100k"
    assert by_id["SN500"]["pricing"]["price_tier"] == "$100k-$150k"
    assert by_id["SN600"]["pricing"]["price_tier"] == "$150k+"
    # ValueError invoice branch: garbage stays None, home still converted
    assert by_id["SN700"]["pricing"]["invoice_amount"] is None


def test_convert_inventory_main_block_runs(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_convert_inventory_xlsx(data_dir / "House Orders.xlsx")

    real_dirname = os.path.dirname
    os.path.dirname = lambda _p: str(tmp_path)  # noqa: A001
    try:
        runpy.run_path(str(CONVERT_INVENTORY_PATH), run_name="__main__")
    finally:
        os.path.dirname = real_dirname  # noqa: A001

    assert (data_dir / "inventory.json").exists()
    assert "Processed 7 items." in capsys.readouterr().out


# ─── seo_routes ─────────────────────────────────────────────────────────────

FAKE_SEO_HOMES = [
    {
        "id": "43372",
        "legacy_inventory_id": "43372",
        "model_name": "Premier / Creole 3256H32447",
        "manufacturer": "Champion Homes",
        "classification": "Double Wide",
        "status": "Available",
        "detail_url": "https://www.texashomeoutlet.com/inventory-detail/43372/texas-home-outlet/huffman/premier/",
        "quote_url": "https://www.texashomeoutlet.com/quote/inventory/123/43372",
        "hero_image": "https://img.example.com/creole.jpg",
        "price_value": 129900,
        "specs": {"beds": 4, "baths": 2, "dimensions": "32x56"},
        "description": "Spacious double-section home.",
    },
    {
        "id": "floorplan-223034",
        "legacy_plan_id": "223034",
        "model_name": "Skyliner 4732B",
        "manufacturer": "Skyline",
        "classification": "Single Wide",
        "status": "Orderable",
        "detail_url": "https://www.texashomeoutlet.com/plan/223034/skyliner/4732b/",
        "quote_url": "https://www.texashomeoutlet.com/quote/plan/223034",
        "price_value": None,
        "specs": {"beds": 3, "baths": 2},
    },
    {
        "id": "44490",
        "stock_number": "44490",
        "model_name": "PRE-OWNED / Big Blue",
        "manufacturer": "Pre-Owned",
        "classification": "Single Wide",
        "status": "Pre-Owned",
        "inventory_kind": "pre_owned",
        "display_price": "Call for Price",
        "price_value": None,
        "image_url": "https://img.example.com/lot-44490.jpg",
        "real_photos": ["https://img.example.com/lot-44490.jpg"],
    },
]


def _seo_client(monkeypatch):
    from test_api_v1 import create_client

    client, main, db, logger = create_client(monkeypatch)
    import seo_routes

    monkeypatch.setattr(seo_routes, "_get_homes", lambda: FAKE_SEO_HOMES)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
    return client, seo_routes


def test_seo_first_image_preowned_and_photo_dict_branches(monkeypatch):
    import seo_routes

    # pre-owned home returns its own lot photo (never a catalog hero)
    home = {"inventory_kind": "pre_owned", "real_photos": ["https://img.example.com/own-lot-photo.jpg"]}
    assert seo_routes._first_image(home) == "https://img.example.com/own-lot-photo.jpg"

    # pre-owned home with no usable own photos -> None
    assert seo_routes._first_image({"inventory_kind": "pre_owned"}) is None

    # new home: photos list whose first entry is a dict -> uses its url key
    home = {"photos": [{"url": "https://img.example.com/dict-photo.jpg"}]}
    assert seo_routes._first_image(home) == "https://img.example.com/dict-photo.jpg"
    # plain string entries work too
    home = {"real_photos": ["https://img.example.com/plain-photo.jpg"]}
    assert seo_routes._first_image(home) == "https://img.example.com/plain-photo.jpg"


def test_seo_inventory_hero_image_falls_back_to_orderable(monkeypatch):
    import seo_routes

    homes = [
        {"status": "Orderable", "hero_image": "https://img.example.com/orderable.jpg"},
        {"status": "Available"},  # no image at all
    ]
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: homes)
    assert seo_routes._inventory_hero_image() == "https://img.example.com/orderable.jpg"

    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [{"status": "Available"}])
    assert seo_routes._inventory_hero_image() is None


def test_seo_load_homes_without_wiring_returns_empty(monkeypatch):
    import seo_routes

    monkeypatch.setattr(seo_routes, "_get_homes", None)
    assert seo_routes._load_homes() == ([], True)
    assert seo_routes._safe_homes() == []


def test_seo_registry_skips_detail_urls_that_match_no_route(monkeypatch):
    import seo_routes

    homes = [
        {
            "id": "1",
            "model_name": "Bourbon",
            "status": "Available",
            "detail_url": "https://www.texashomeoutlet.com/not-a-detail-page",
        }
    ]
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: homes)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
    reg = seo_routes._registry()
    assert reg["detail_by_route"] == {}


def test_seo_local_business_jsonld_prefixes_relative_image(monkeypatch):
    import seo_routes

    monkeypatch.setattr(seo_routes, "get_business", lambda: {"image": "/og-card.png"})
    monkeypatch.setenv("OG_IMAGE_URL", "")
    monkeypatch.setattr(seo_routes, "_get_canonical_base", lambda: "https://www.texashomeoutlet.com")
    data = seo_routes._local_business_jsonld()
    assert data["image"] == "https://www.texashomeoutlet.com/og-card.png"


def test_seo_product_jsonld_availability_branches(monkeypatch):
    import seo_routes

    base = {"price_value": 100000, "model_name": "Bourbon"}
    pre_order = seo_routes._product_jsonld({**base, "status": "Orderable"}, "https://x/p")
    assert pre_order["offers"]["availability"] == "https://schema.org/PreOrder"
    sold = seo_routes._product_jsonld({**base, "status": "Sold"}, "https://x/p")
    assert sold["offers"]["availability"] == "https://schema.org/OutOfStock"
    in_stock = seo_routes._product_jsonld(
        {**base, "status": "Available", "availability_verified": True}, "https://x/p"
    )
    assert in_stock["offers"]["availability"] == "https://schema.org/InStock"


def test_seo_shell_falls_back_when_index_missing(monkeypatch, tmp_path):
    import seo_routes

    monkeypatch.setattr(seo_routes, "_index_html_path", str(tmp_path / "missing.html"))
    monkeypatch.setattr(seo_routes, "_shell_cache", None)
    shell = seo_routes._shell()
    assert '<div id="root"></div>' in shell


def test_seo_listing_image_alt_all_subject_branches():
    import seo_routes

    home = {"model_name": "Bourbon"}
    assert seo_routes._listing_image_alt(home, "https://x/fp.jpg", 1, 1, floorplan=True) == (
        "Floor plan for Bourbon"
    )
    alt = seo_routes._listing_image_alt(home, "https://x/ext-2.jpg", 2, 3)
    assert "exterior photo 2" in alt
    alt = seo_routes._listing_image_alt(home, "https://x/ext-2.jpg", 1, 1)
    assert "exterior photo" in alt and " 1" not in alt.split("exterior photo")[-1]
    alt = seo_routes._listing_image_alt(home, "https://x/no-subject.jpg", 2, 3)
    assert "manufactured home photo 2" in alt
    alt = seo_routes._listing_image_alt(home, "https://x/no-subject.jpg", 1, 1)
    assert "manufactured home" in alt and "photo" not in alt


def test_seo_inject_handles_shell_without_root_div():
    import seo_routes

    out = seo_routes._inject("<html><head></head><body></body></html>", "<title>x</title>", "<p>body</p>")
    assert "<p>body</p>" not in out  # no-op replacement keeps shell intact


def test_seo_configure_without_index_html_path(monkeypatch):
    import seo_routes

    original_path = seo_routes._index_html_path
    seo_routes.configure(lambda: [], lambda: "https://base.example")
    try:
        assert seo_routes._index_html_path == original_path
        assert seo_routes._base() == "https://base.example"
    finally:
        seo_routes._get_homes = None
        seo_routes._get_canonical_base = None


def test_seo_configure_with_index_html_path_overrides():
    import seo_routes

    original_path = seo_routes._index_html_path
    try:
        seo_routes.configure(
            lambda: [], lambda: "https://base.example", index_html_path="/tmp/other.html"
        )
        assert seo_routes._index_html_path == "/tmp/other.html"
    finally:
        seo_routes._index_html_path = original_path
        seo_routes._get_homes = None
        seo_routes._get_canonical_base = None


def test_seo_canonical_listing_path_rejects_bad_tokens(monkeypatch):
    import seo_routes

    monkeypatch.setattr(seo_routes, "_get_homes", lambda: FAKE_SEO_HOMES)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    assert seo_routes.canonical_listing_path_for_id("") is None
    assert seo_routes.canonical_listing_path_for_id("has/slash") is None
    assert seo_routes.canonical_listing_path_for_id("x" * 81) is None
    # unknown id -> falls through the loop
    assert seo_routes.canonical_listing_path_for_id("nope") is None
    # known id resolves to its listing path
    path = seo_routes.canonical_listing_path_for_id("44490")
    assert path and path.startswith("/")


def test_seo_canonical_listing_path_none_when_inventory_down(monkeypatch):
    import seo_routes

    def _boom():
        raise RuntimeError("inventory backend down")

    monkeypatch.setattr(seo_routes, "_get_homes", _boom)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
    assert seo_routes.canonical_listing_path_for_id("43372") is None


def test_seo_canonical_listing_path_none_when_home_has_no_paths(monkeypatch):
    import seo_routes

    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [{"id": "lonely"}])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
    monkeypatch.setattr(seo_routes, "listing_path", lambda _home: None)
    assert seo_routes.canonical_listing_path_for_id("lonely") is None


def _fake_registry(monkeypatch, *, homes, detail_by_route=None, detail_path_by_route=None):
    import seo_routes

    reg = {
        "inventory_ok": True,
        "homes": homes,
        "detail_by_route": detail_by_route or {},
        "detail_path_by_route": detail_path_by_route or {},
        "quote_redirects": {},
        "quote_alias_redirects": {},
        "instock_by_stock": {},
        "instock_path_by_stock": {},
    }
    monkeypatch.setattr(seo_routes, "_registry", lambda: reg)
    return seo_routes


def test_seo_itemlist_jsonld_all_loop_branches(monkeypatch):
    home_a = {"id": "a", "model_name": "Alpha"}
    home_b = {"id": "b", "model_name": "Beta"}
    detail_by_route = {
        ("inventory-detail", "1"): home_a,
        ("inventory-detail", "2"): home_b,
    }
    seo = _fake_registry(
        monkeypatch,
        homes=[home_a, home_b],
        detail_by_route=detail_by_route,
        detail_path_by_route={
            ("inventory-detail", "1"): "/inventory-detail/1/a/",
            ("inventory-detail", "2"): "/inventory-detail/2/b/",
        },
    )
    monkeypatch.setattr(seo, "_base", lambda: "https://base.example")
    monkeypatch.setattr(
        seo,
        "listing_path",
        lambda home: f"/homes/{home['id']}-slug" if home["id"] == "b" else None,
    )

    # limit reached inside the first (detail) loop
    itemlist = seo._inventory_itemlist_jsonld(limit=1)
    assert len(itemlist["itemListElement"]) == 1
    assert itemlist["itemListElement"][0]["position"] == 1

    # stale route key with no detail path -> skipped (1464 branch);
    # second loop: home b's listing path equals an already-emitted detail URL
    # -> deduped via seen_urls; home a has no listing path.
    reg = seo._registry()
    reg["detail_by_route"][("inventory-detail", "3")] = home_b
    monkeypatch.setattr(
        seo,
        "listing_path",
        lambda home: "/inventory-detail/2/b/" if home["id"] == "b" else None,
    )
    itemlist = seo._inventory_itemlist_jsonld(limit=5)
    urls = [item["url"] for item in itemlist["itemListElement"]]
    assert len(set(urls)) == len(urls)
    assert urls == [
        "https://base.example/inventory-detail/1/a/",
        "https://base.example/inventory-detail/2/b/",
    ]


def test_seo_itemlist_jsonld_second_loop_break_and_empty(monkeypatch):
    homes = [{"id": str(i), "model_name": f"Model {i}"} for i in range(5)]
    seo = _fake_registry(monkeypatch, homes=homes)
    monkeypatch.setattr(seo, "listing_path", lambda home: f"/homes/{home['id']}")
    monkeypatch.setattr(seo, "_base", lambda: "https://base.example")

    # detail loop contributes nothing; second loop fills to the limit and breaks
    itemlist = seo._inventory_itemlist_jsonld(limit=3)
    assert len(itemlist["itemListElement"]) == 3
    assert itemlist["itemListElement"][2]["position"] == 3

    # no paths at all -> None
    monkeypatch.setattr(seo, "listing_path", lambda _home: None)
    assert seo._inventory_itemlist_jsonld(limit=3) is None


def test_seo_render_spa_response_falls_back_on_internal_error(monkeypatch):
    import seo_routes

    def _boom(_path):
        raise RuntimeError("rendering exploded")

    monkeypatch.setattr(seo_routes, "_render_spa_response", _boom)
    assert seo_routes.render_spa_response("/inventory") is None


def test_seo_crawlable_home_listing_block_floorplan_figure():
    import seo_routes

    home = {
        "stock_number": "50001",
        "model_name": "Plan Home 28x40",
        # filename has no floorplan token, so the picker keeps it as a photo;
        # the block still recognizes it as THE floor plan via floor_plan_url.
        "floor_plan_url": "https://x.example/fp-1.jpg",
        "real_photos": ["https://x.example/fp-1.jpg", "https://x.example/ext-1.jpg"],
        "specs": {"beds": 3, "baths": 2},
    }
    block = seo_routes._crawlable_home_listing_block(home)
    assert "<figcaption>Floor plan</figcaption>" in block
    assert '<img src="https://x.example/ext-1.jpg"' in block
    assert "Stock #50001" in block


def test_seo_legacy_home_aliases_301_to_inventory(monkeypatch):
    client, _seo = _seo_client(monkeypatch)
    for legacy in ("/home", "/index.html"):
        response = client.get(legacy, follow_redirects=False)
        assert response.status_code == 301, legacy
        assert response.headers["location"] == "/inventory"


def test_seo_chat_route_has_no_crawlable_block(monkeypatch):
    client, _seo = _seo_client(monkeypatch)
    response = client.get("/chat")
    assert response.status_code == 200


def test_seo_instock_trailing_slash_301_and_outage_503(monkeypatch):
    client, seo = _seo_client(monkeypatch)
    response = client.get("/homes/44490-big-blue/", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"].rstrip("/").endswith("/homes/44490-big-blue")

    def _boom():
        raise RuntimeError("inventory backend down")

    monkeypatch.setattr(seo, "_get_homes", _boom)
    monkeypatch.setattr(seo, "_registry_cache", None)
    monkeypatch.setattr(seo, "_registry_built_at", 0.0)
    response = client.get("/homes/44490-big-blue")
    assert response.status_code == 503


# ─── database.models: validators ────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "google_ads_launch_draft.json").read_text())
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
FIVE_MIN = timedelta(minutes=5)
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
DIGEST = contract_sha256(CONTRACT)
DEPLOYMENT_ID = deployment_id(CONTRACT)
DEPLOYMENT_KEY = CONTRACT["deployment"]["key"]
CONTRACT_HASH = f"sha256:{DIGEST}"
CONTRACT_LABEL = f"tho-contract-{DIGEST[:12]}"


def _deployment_payload(**overrides):
    payload = {
        "deployment_id": DEPLOYMENT_ID,
        "deployment_key": DEPLOYMENT_KEY,
        "contract_hash": CONTRACT_HASH,
        "contract_label": CONTRACT_LABEL,
        "state": "INTERNAL_DRAFT",
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_models_deployment_record_happy_paths():
    record = GoogleAdsDeploymentRecord.model_validate(_deployment_payload())
    assert record.state == "INTERNAL_DRAFT"

    approved = GoogleAdsDeploymentRecord.model_validate(
        _deployment_payload(
            state="PAUSED_CREATE_APPROVED",
            version=3,
            worker_claim_hash=HASH_A,
            claim_expires_at=NOW + FIVE_MIN,
        )
    )
    assert approved.worker_claim_hash == HASH_A

    fenced = GoogleAdsDeploymentRecord.model_validate(
        _deployment_payload(
            state="PAUSED_CREATE_APPROVED",
            version=3,
            updated_at=NOW + FIVE_MIN,
            worker_claim_hash=HASH_A,
            claim_expires_at=NOW + FIVE_MIN,
            create_fenced_at=NOW + timedelta(minutes=1),
            create_fence_claim_hash=HASH_B,
        )
    )
    assert fenced.create_fenced_at is not None

    created = GoogleAdsDeploymentRecord.model_validate(
        _deployment_payload(
            state="PAUSED_CREATED",
            version=4,
            provider_reference_hash=HASH_A,
            create_fenced_at=NOW,
        )
    )
    assert created.state == "PAUSED_CREATED"


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"worker_claim_hash": HASH_A}, "set together"),
        ({"claim_expires_at": NOW + FIVE_MIN}, "set together"),
        (
            {
                "state": "INTERNAL_DRAFT",
                "error_code": "provider_create_failed",
            },
            "errors are allowed only",
        ),
        (
            {"state": "PAUSED_CREATED", "version": 4, "create_fenced_at": NOW},
            "requires a provider reference hash",
        ),
        (
            {"state": "PAUSED_CREATED", "version": 4, "provider_reference_hash": HASH_A},
            "requires a durable create fence",
        ),
        (
            {
                "state": "PAUSED_CREATED",
                "version": 4,
                "provider_reference_hash": HASH_A,
                "create_fenced_at": NOW,
                "create_fence_claim_hash": HASH_B,
            },
            "cannot retain a fence claimant",
        ),
        (
            {"provider_reference_hash": HASH_A},
            "allowed only after paused creation",
        ),
        ({"create_fenced_at": NOW}, "allowed only after paused-create approval"),
        (
            {
                "state": "PAUSED_CREATE_APPROVED",
                "version": 3,
                "worker_claim_hash": HASH_A,
                "claim_expires_at": NOW + FIVE_MIN,
                "create_fenced_at": NOW + timedelta(minutes=10),
                "create_fence_claim_hash": HASH_B,
            },
            "must be within the record lifetime",
        ),
        (
            {
                "state": "PAUSED_CREATE_APPROVED",
                "version": 3,
                "updated_at": NOW + timedelta(minutes=2),
                "worker_claim_hash": HASH_A,
                "claim_expires_at": NOW + FIVE_MIN,
                "create_fenced_at": NOW + timedelta(minutes=1),
            },
            "must retain hashed claimants",
        ),
        (
            {
                "state": "PAUSED_CREATE_APPROVED",
                "version": 3,
                "worker_claim_hash": HASH_A,
                "claim_expires_at": NOW + FIVE_MIN,
                "create_fence_claim_hash": HASH_B,
            },
            "fence claimant requires a durable create fence",
        ),
        (
            {
                "state": "PAUSED_CREATE_APPROVED",
                "version": 3,
                "worker_claim_hash": HASH_A,
                "claim_expires_at": NOW + FIVE_MIN,
                "error_code": "provider_create_failed",
            },
            "pre-fence failed claim",
        ),
        ({"updated_at": NOW - FIVE_MIN}, "cannot precede created_at"),
        (
            {
                "state": "PAUSED_CREATE_APPROVED",
                "version": 3,
                "updated_at": NOW + timedelta(minutes=10),
                "worker_claim_hash": HASH_A,
                "claim_expires_at": NOW + FIVE_MIN,
                "create_fenced_at": NOW + timedelta(minutes=6),
                "create_fence_claim_hash": HASH_B,
            },
            "must precede claim expiry",
        ),
    ],
)
def test_models_deployment_record_raise_branches(overrides, match):
    with pytest.raises(ValidationError, match=match):
        GoogleAdsDeploymentRecord.model_validate(_deployment_payload(**overrides))


def test_models_require_utc_rejects_naive_timestamps():
    with pytest.raises(ValidationError, match="timezone-aware"):
        GoogleAdsDeploymentRecord.model_validate(
            _deployment_payload(created_at=datetime(2026, 8, 12, 12, 0))
        )


def _operation_key_payload(**overrides):
    payload = {
        "deployment_id": DEPLOYMENT_ID,
        "contract_hash": CONTRACT_HASH,
        "key_hash": HASH_A,
        "created_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_models_operation_key_identity_mismatch():
    GoogleAdsOperationKeyRecord.model_validate(_operation_key_payload())
    with pytest.raises(ValidationError, match="operation deployment identity"):
        GoogleAdsOperationKeyRecord.model_validate(
            _operation_key_payload(deployment_id=f"{DEPLOYMENT_KEY}-other--{'c' * 64}")
        )


def _access_evidence_payload(**overrides):
    evidence = build_access_evidence(
        deployment_id=DEPLOYMENT_ID,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + FIVE_MIN,
        source_revision="a" * 40,
        now=NOW,
    )
    payload = evidence_payload(evidence)
    payload.update(overrides)
    return payload


def test_models_access_evidence_raise_branches():
    GoogleAdsAccessEvidenceRecord.model_validate(_access_evidence_payload())
    with pytest.raises(ValidationError, match="expiry must follow observation"):
        GoogleAdsAccessEvidenceRecord.model_validate(
            _access_evidence_payload(expires_at=NOW)
        )

    # defensive branch: digest computation blowing up maps to a validation error
    import database.models as models_module

    def _explode(**_kwargs):
        raise ValueError("digest backend down")

    original = models_module.compute_evidence_digest
    models_module.compute_evidence_digest = _explode
    try:
        with pytest.raises(ValidationError, match="access evidence is invalid"):
            GoogleAdsAccessEvidenceRecord.model_validate(_access_evidence_payload())
    finally:
        models_module.compute_evidence_digest = original


def _step_up_payload(**overrides):
    caps = GoogleAdsStepUpCapsRecord(
        average_daily_usd=10,
        max_single_day_charge_usd=10,
        monthly_charge_limit_usd=100,
        max_cpc_usd=1,
    )
    context = {
        "caps": caps.model_dump(mode="json"),
        "contract_hash": CONTRACT_HASH,
        "deployment_id": DEPLOYMENT_ID,
        "evidence_digest": HASH_A,
        "purpose": "PAUSED_CREATE",
    }
    import hashlib as _hashlib

    raw_context = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    context_digest = "sha256:" + _hashlib.sha256(raw_context).hexdigest()
    raw_id = "|".join((HASH_B, context_digest, HASH_A, HASH_A)).encode()
    evidence_id = "sha256:" + _hashlib.sha256(raw_id).hexdigest()
    payload = {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "deployment_id": DEPLOYMENT_ID,
        "contract_hash": CONTRACT_HASH,
        "caps": caps,
        "evidence_digest": HASH_A,
        "context_digest": context_digest,
        "nonce_hash": HASH_B,
        "owner_email_hash": HASH_A,
        "credential_id_hash": HASH_A,
        "verified_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_models_step_up_evidence_raise_branches():
    GoogleAdsOwnerStepUpEvidenceRecord.model_validate(_step_up_payload())
    with pytest.raises(ValidationError, match="step-up deployment identity"):
        GoogleAdsOwnerStepUpEvidenceRecord.model_validate(
            _step_up_payload(deployment_id=f"{DEPLOYMENT_KEY}-other--{'c' * 64}")
        )
    with pytest.raises(ValidationError, match="context digest mismatch"):
        GoogleAdsOwnerStepUpEvidenceRecord.model_validate(
            _step_up_payload(context_digest=HASH_B)
        )
    with pytest.raises(ValidationError, match="evidence identity mismatch"):
        GoogleAdsOwnerStepUpEvidenceRecord.model_validate(
            _step_up_payload(evidence_id=HASH_B)
        )


def _proof_payload(**overrides):
    payload = {
        "proof_id": HASH_A,
        "proof_reference_hash": HASH_B,
        "deployment_id": DEPLOYMENT_ID,
        "contract_hash": CONTRACT_HASH,
        "access_evidence_id": HASH_A,
        "owner_email_hash": HASH_B,
        "authority_from_version": 2,
        "authority_to_version": 3,
        "consumed_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_models_proof_deployment_mismatch():
    GoogleAdsPausedCreateApprovalProofRecord.model_validate(_proof_payload())
    with pytest.raises(ValidationError, match="proof deployment identity"):
        GoogleAdsPausedCreateApprovalProofRecord.model_validate(
            _proof_payload(deployment_id=f"{DEPLOYMENT_KEY}-other--{'c' * 64}")
        )


def _outbox_payload(**overrides):
    payload = {
        "deployment_id": DEPLOYMENT_ID,
        "contract_hash": CONTRACT_HASH,
        "proof_id": HASH_A,
        "access_evidence_id": HASH_A,
        "state": "PENDING",
        "attempt_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_models_outbox_happy_paths():
    GoogleAdsPausedCreateOutboxRecord.model_validate(_outbox_payload())
    GoogleAdsPausedCreateOutboxRecord.model_validate(
        _outbox_payload(
            state="DISPATCHING",
            attempt_count=1,
            dispatcher_claim_hash=HASH_A,
            claim_expires_at=NOW + FIVE_MIN,
        )
    )
    GoogleAdsPausedCreateOutboxRecord.model_validate(
        _outbox_payload(state="DISPATCHED", attempt_count=1, dispatched_at=NOW)
    )
    GoogleAdsPausedCreateOutboxRecord.model_validate(
        _outbox_payload(
            state="FAILED",
            attempt_count=3,
            error_code="dispatch_attempts_exhausted",
        )
    )


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"deployment_id": f"{DEPLOYMENT_KEY}-other--{'c' * 64}"}, "outbox deployment identity"),
        ({"updated_at": NOW - FIVE_MIN}, "update cannot precede creation"),
        ({"dispatcher_claim_hash": HASH_A}, "set together"),
        ({"claim_expires_at": NOW + FIVE_MIN}, "set together"),
        ({"state": "DISPATCHING"}, "requires one live claim"),
        (
            {
                "state": "DISPATCHING",
                "dispatcher_claim_hash": HASH_A,
                "claim_expires_at": NOW + FIVE_MIN,
                "dispatched_at": NOW,
            },
            "requires one live claim",
        ),
        (
            {"state": "DISPATCHED", "dispatcher_claim_hash": HASH_A,
             "claim_expires_at": NOW + FIVE_MIN},
            "only dispatching outbox may retain a claim",
        ),
        ({"state": "DISPATCHED"}, "requires a clean dispatch timestamp"),
        (
            {"state": "DISPATCHED", "attempt_count": 1, "dispatched_at": NOW,
             "error_code": "job_invocation_failed"},
            "requires a clean dispatch timestamp",
        ),
        ({"dispatched_at": NOW}, "cannot have a dispatch timestamp"),
        (
            {"error_code": "job_invocation_failed"},
            "requires an attempted dispatch",
        ),
        (
            {"state": "FAILED", "attempt_count": 1, "error_code": "worker_failed"},
            "exhausted sanitized attempts",
        ),
        ({"state": "FAILED", "attempt_count": 3}, "exhausted sanitized attempts"),
    ],
)
def test_models_outbox_raise_branches(overrides, match):
    with pytest.raises(ValidationError, match=match):
        GoogleAdsPausedCreateOutboxRecord.model_validate(_outbox_payload(**overrides))


def test_models_customer_validators_raise():
    Customer(full_name="Jane Buyer", email="jane@example.com", phone="2815551234")
    with pytest.raises(ValidationError, match="Invalid email format"):
        Customer(full_name="Jane Buyer", email="not-an-email")
    with pytest.raises(ValidationError, match="at least 10 digits"):
        Customer(full_name="Jane Buyer", phone="12345")


def test_models_inventory_negative_specs_give_field_error():
    # ge=0 constraints reject negatives before the custom validator runs;
    # the defensive validator branch itself is unreachable via model_validate.
    with pytest.raises(ValidationError):
        Inventory(serial_number="SN1", model_name="Bourbon", bedrooms=-1)


def test_models_inventory_positive_specs_pass_validator_guard():
    home = Inventory(
        serial_number="SN1",
        model_name="Bourbon",
        bedrooms=3,
        bathrooms=2,
        sqft=2432,
        status="AVAILABLE",
        msrp=55000.0,
    )
    assert home.bedrooms == 3


def test_models_deal_to_document_data_mailing_and_identity_branches():
    deal = Deal(
        buyer_first_name="Jane",
        buyer_last_name="Buyer",
        mailing_address="123 Main St",
        mailing_city="Huffman",
        mailing_zip="77336",
        manufacturer="Champion",
        serial_number_2="SN2",
        label_number_2="LBL2",
    )
    data = deal.to_document_data()
    # mailing composition: address + city/state/zip appended
    assert data.get("buyer_full_address") is None  # no installation address
    assert "mailing_full_address" in data
    mailing = data["mailing_full_address"]
    assert mailing.startswith("123 Main St")
    assert "Huffman, TX 77336" in mailing
    # manufacturer-only branch
    assert data["manufacturer_model"] == "Champion"
    # serial/label combined picks up serial_number_2 + label_number_2
    combined = data["serial_label_combined"]
    assert "S/N2: SN2" in combined
    assert "HUD2: LBL2" in combined

    model_only = Deal(model="Creole 3276").to_document_data()
    assert model_only["manufacturer_model"] == "Creole 3276"

    neither = Deal().to_document_data()
    assert neither.get("manufacturer_model") is None


def test_models_deal_to_document_data_full_identity_branches():
    deal = Deal(
        mailing_address="PO Box 1",
        mailing_city="Huffman",
        mailing_zip="77336",
        manufacturer="Champion",
        model="Creole",
        serial_number_1="SN1",
        serial_number_2="SN2",
        label_number_1="LBL1",
        label_number_2="LBL2",
    )
    data = deal.to_document_data()
    assert data["manufacturer_model"] == "Champion Creole"
    combined = data["serial_label_combined"]
    assert "S/N: SN1" in combined
    assert "S/N2: SN2" in combined
    assert "HUD: LBL1" in combined
    assert "HUD2: LBL2" in combined
