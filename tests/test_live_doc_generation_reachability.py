"""Live-path doc-generation reachability gate (#230 incident class).

The #230 class of bug: a derivation is wired into the WRONG engine path. The
escrow math first landed only in the legacy ``document_engine._compute_fields``,
which the live server no longer calls — so the figures were computed on a dead
path and never reached the PDF the customer signs. Unit tests on the helper were
green while the real output was blank.

This gate asserts on REAL OUTPUT through the SAME entrypoint the live server
uses (``main`` → ``engine_generate_document`` → ``tools.document_engine_v2``
→ ``tools.document_quality.enrich_document_data``), never on an internal helper:

  1. The live entrypoint identity is pinned — main.engine_generate_document must
     resolve to the v2 engine's generate_document, which runs enrich (not the
     legacy _compute_fields). If a refactor repoints the server at a dead path,
     this fails.
  2. A sample Deal carrying the escrow inputs (annual_insurance, tax_rate,
     taxable_value) is generated through that entrypoint, and the produced
     Internal_ImportantNoticeTax.pdf AcroForm is read back and asserted to
     actually contain the computed escrow values.

CI-safe: reads AcroForm ``/V`` values back out (no poppler / rasterization),
GCS uploads disabled via the OUTPUT_DIR + upload_to_gcs monkeypatches, and the
output is written to a tmp dir.
"""

from __future__ import annotations

import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEMPLATE = "Internal_ImportantNoticeTax.pdf"


def _read_acroform_values(pdf_path: str) -> dict[str, str]:
    reader = PdfReader(pdf_path)
    out: dict[str, str] = {}
    for name, fld in (reader.get_fields() or {}).items():
        v = fld.get("/V")
        if v is not None:
            out[str(name)] = str(v)
    return out


def test_live_server_entrypoint_resolves_to_v2_engine_not_legacy():
    """The server's document entrypoint must be the v2 (enrich) path.

    Pins the #230 fix at the wiring level: ``main.engine_generate_document`` is
    the function the live ``/api/documents/generate`` and deal endpoints call.
    It must be the v2 engine's ``generate_document`` (which runs
    ``enrich_document_data``), NOT the legacy engine that uses
    ``_compute_fields``. If a refactor repoints the server, this fails before
    any customer gets a blank form.
    """
    import main
    from tools.document_engine_v2 import generate_document as v2_generate

    # main re-exports the v2 engine entrypoint under this alias.
    assert main.engine_generate_document is v2_generate, (
        "main.engine_generate_document no longer points at the v2 engine — the "
        "live server may be on a dead derivation path (the #230 class)."
    )

    # And the v2 path runs the canonical enrichment, not the legacy compute.
    import inspect

    import tools.document_engine_v2 as v2

    src = inspect.getsource(v2.DocumentEngineV2.generate_document)
    assert "enrich_document_data" in src, (
        "v2 generate_document no longer calls enrich_document_data — escrow/derived "
        "fields would not reach the PDF (the #230 class)."
    )


def test_escrow_inputs_reach_pdf_through_live_entrypoint(tmp_path, monkeypatch):
    """A Deal's escrow inputs must render on the real form via the LIVE path.

    Drives ``main.engine_generate_document`` (the exact callable the live
    endpoints invoke) with a sample Deal that carries the escrow inputs, then
    reads the produced AcroForm back out and asserts the COMPUTED escrow values
    are present. On the #230 defect these widgets were blank because the math
    ran on a path the server doesn't call.
    """
    import main
    import tools.document_tools as dt
    from database.models import Deal
    from scripts.validate_document_fills import FULL_DEAL

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    # Build a real Deal including the escrow inputs, then serialize it the way
    # the server does (Deal.to_document_data) so this exercises the production
    # data shape, not a hand-rolled dict.
    base = dict(FULL_DEAL)
    deal = Deal(
        buyer_name=base["buyer_name"],
        buyer_first_name=base["buyer_first_name"],
        buyer_last_name=base["buyer_last_name"],
        buyer_county=base["buyer_county"],
        sales_price=100000,
        annual_insurance=1200,
        tax_rate=2.5,
        taxable_value=100000,
    )
    data = {**base, **deal.to_document_data()}

    # The exact callable main exposes to /api/documents/generate and the deal
    # endpoints — NOT enrich_document_data, NOT the legacy engine.
    result = main.engine_generate_document(TEMPLATE, dict(data))
    assert result["success"], result.get("message")

    actual = _read_acroform_values(result["file_path"])
    assert actual, "no AcroForm values written (live path produced an empty form)"

    def widget(name: str) -> str:
        return f"topmostSubform[0].Page1[0].{name}"

    # insurance_premium_monthly = 1200/12 = 100.00
    assert actual.get(widget("Insurance_Premium_Monthly[0]")) == "100.00"
    # escrow_value = taxable_value = 100,000.00
    assert actual.get(widget("Escrow_Value[0]")) == "100,000.00"
    # tax_escrow_yearly = 2.5% * 100000 = 2,500.00
    assert actual.get(widget("Tax_Escrow_Yearly[0]")) == "2,500.00"
    # tax_escrow_payment = 2500/12 = 208.33
    assert actual.get(widget("Tax_Escrow_Pmt[0]")) == "208.33"
    # tax_escrow_pct = the entered rate
    assert actual.get(widget("Tax_Escrow_Pct[0]")) in ("2.5", "2.50")
