"""Seller/retailer name varies by form family (client requirement, 2026-06-05):
TMHA forms read the short trade name "Texas Home Outlet"; TDHCA, State, and
Internal forms read the full legal entity "Prosperity Acquisitions, Inc. dba
Texas Home Outlet".
"""

import os
import shutil
import subprocess

import pytest

from scripts.validate_document_fills import FULL_DEAL
from tools.document_engine_v2 import (
    SELLER_NAME_LEGAL,
    SELLER_NAME_SHORT,
    _seller_name_for_category,
    generate_document,
)
from tools.document_tools import OUTPUT_DIR


def test_category_to_seller_name_logic():
    assert _seller_name_for_category("TMHA") == SELLER_NAME_SHORT
    assert _seller_name_for_category("tmha") == SELLER_NAME_SHORT
    assert _seller_name_for_category("TDHCA") == SELLER_NAME_LEGAL
    assert _seller_name_for_category("State") == SELLER_NAME_LEGAL
    assert _seller_name_for_category("Internal") == SELLER_NAME_LEGAL
    assert _seller_name_for_category("") == SELLER_NAME_LEGAL
    assert _seller_name_for_category(None) == SELLER_NAME_LEGAL


def _rendered_text(filename: str) -> str:
    path = os.path.join(OUTPUT_DIR, filename)
    return subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout


@pytest.mark.skipif(
    shutil.which("pdftotext") is None,
    reason="pdftotext (poppler-utils) not installed; render check skipped",
)
def test_tmha_uses_short_name_tdhca_uses_legal_entity():
    # TMHA form -> short trade name only (no legal entity).
    r_tmha = generate_document("TMHA_SalesContract.pdf", dict(FULL_DEAL))
    assert r_tmha["success"], r_tmha.get("message")
    tmha_text = _rendered_text(r_tmha["filename"])
    assert "Prosperity Acquisitions" not in tmha_text
    assert "Texas Home Outlet" in tmha_text

    # TDHCA form -> full registered legal entity.
    r_tdhca = generate_document("TDHCA_1023-Statement-Ownership.pdf", dict(FULL_DEAL))
    assert r_tdhca["success"], r_tdhca.get("message")
    tdhca_text = _rendered_text(r_tdhca["filename"])
    assert "Prosperity Acquisitions" in tdhca_text
