"""Guard tests: the business address / zip / phone must have a single source.

Background: the showroom address was duplicated as string literals across many
modules. One of them used Humble's zip (77338) instead of Huffman's (77336),
which shipped to the live site. These tests make that class of bug impossible to
reintroduce — they fail if any scanned source disagrees with config.yaml.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import config_loader

REPO = Path(__file__).resolve().parent.parent
EXPECTED_ZIP = "77336"          # Huffman, TX
WRONG_ZIP = "77338"             # Humble, TX — the bug we fixed; must appear nowhere

# Files that legitimately reference the physical business address.
SCANNED = [
    "config.yaml",
    "email_service.py",
    "esign_consent.py",
    "tools/lead_nurture.py",
    "tools/crm_tools.py",
    "tools/document_tools.py",
    "tools/document_quality.py",
    "database/models.py",
    "frontend/src/constants.js",
]


def _read(rel: str) -> str | None:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else None


def test_config_is_the_canonical_source():
    assert config_loader.business_zip() == EXPECTED_ZIP
    assert config_loader.business_city() == "Huffman"
    assert config_loader.business_state() == "TX"
    assert EXPECTED_ZIP in config_loader.business_address()
    assert config_loader.business_city() in config_loader.business_address()
    assert config_loader.business_city_state_zip() == "Huffman, TX 77336"


def test_wrong_zip_appears_in_no_scanned_source():
    offenders = [rel for rel in SCANNED if (txt := _read(rel)) and WRONG_ZIP in txt]
    assert not offenders, f"Wrong zip {WRONG_ZIP} (Humble) found in: {offenders}"


def test_every_huffman_tx_zip_matches_config():
    pattern = re.compile(r"Huffman,?\s+TX\s+(\d{5})")
    mismatches = []
    for rel in SCANNED:
        txt = _read(rel)
        if txt is None:
            continue
        mismatches += [(rel, zc) for zc in pattern.findall(txt) if zc != EXPECTED_ZIP]
    assert not mismatches, f"Zip(s) disagree with config {EXPECTED_ZIP}: {mismatches}"


def test_email_service_constants_come_from_config():
    import email_service

    assert email_service.BUSINESS_ADDRESS == config_loader.business_address()
    assert email_service.BUSINESS_PHONE == config_loader.business_phone()
    assert EXPECTED_ZIP in email_service.BUSINESS_ADDRESS


def test_document_quality_zip_agrees_with_config():
    from tools import document_quality

    assert document_quality.BUSINESS_ZIP == config_loader.business_zip()
    assert document_quality.BUSINESS_CITY_STATE_ZIP == config_loader.business_city_state_zip()


def test_esign_consent_defaults_resolve_from_config():
    import esign_consent

    sig = inspect.signature(esign_consent.build_consent_pdf_bytes)
    # Defaults are None so the function resolves the live values from config.yaml
    # at call time (no hardcoded address that can drift / lose the zip).
    assert sig.parameters["business_address"].default is None
    assert sig.parameters["business_phone"].default is None


def test_deal_seller_zip_default_includes_zip():
    from database.models import Deal

    assert Deal.model_fields["seller_zip"].default == EXPECTED_ZIP
