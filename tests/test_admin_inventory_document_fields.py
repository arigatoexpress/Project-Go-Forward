"""Static regression checks for admin inventory document-field pass-through."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN = REPO_ROOT / "main.py"


def test_admin_inventory_api_exposes_document_autofill_fields():
    source = MAIN.read_text()
    assert "def _pick_inventory_field" in source
    for key in (
        '"wind_zone"',
        '"weight_sec_1"',
        '"weight_sec_2"',
        '"date_of_manufacture"',
        '"manufacturer_address"',
        '"manufacturer_city"',
        '"manufacturer_state"',
        '"manufacturer_zip"',
        '"installer_name"',
        '"installer_phone"',
        '"installer_license"',
    ):
        assert key in source
