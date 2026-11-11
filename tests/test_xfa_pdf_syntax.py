"""Single-form PDFs must stay syntactically valid after XFA fill.

Commit de30499 stored the filled XFA datasets stream inline in the AcroForm
/XFA array (``writer_xfa[i + 1] = new_stream``). That embeds a bare stream in
object 123. qpdf then reports ``invalid character in hexstring``, pypdf cannot
``clone_from`` the file, and strict viewers (Chrome on Windows, Edge, Acrobat)
refuse to open it. The merged packet still opened because page merge drops
/XFA. The stream has to be an indirect object.
"""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.document_tools import DOCUMENTS_DIR, fill_pdf_form


def _xfa_template_names() -> list[str]:
    names: list[str] = []
    if not os.path.isdir(DOCUMENTS_DIR):
        return names
    for name in sorted(os.listdir(DOCUMENTS_DIR)):
        if not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(DOCUMENTS_DIR, name)
        try:
            reader = PdfReader(path)
            catalog = reader.trailer.get("/Root") or {}
            acroform = catalog.get("/AcroForm") if catalog else None
            if acroform and acroform.get("/XFA"):
                names.append(name)
        except Exception:
            continue
    return names


XFA_TEMPLATES = _xfa_template_names()


def test_xfa_template_set_is_present():
    """A scan miss would otherwise make the parametrized check pass vacuously."""
    assert XFA_TEMPLATES, f"No XFA templates found in {DOCUMENTS_DIR}"
    assert "TMHA_SalesContract.pdf" in XFA_TEMPLATES
    assert "TDHCA_1023-Statement-Ownership.pdf" in XFA_TEMPLATES


@pytest.mark.parametrize("template_name", XFA_TEMPLATES)
def test_filled_xfa_template_is_openable(template_name, monkeypatch, tmp_path):
    monkeypatch.setenv("THO_DISABLE_GCS_UPLOADS", "1")
    monkeypatch.setattr("tools.document_tools.OUTPUT_DIR", str(tmp_path))

    source = os.path.join(DOCUMENTS_DIR, template_name)
    source_pages = len(PdfReader(source).pages)
    output = fill_pdf_form(
        source,
        {"buyer_name": "Syntax Check", "sales_price": "1.00"},
        f"syntax_{template_name}",
    )

    assert os.path.isfile(output)
    raw = Path(output).read_bytes()
    assert raw.startswith(b"%PDF")
    assert len(PdfReader(output).pages) == source_pages

    with pikepdf.open(output) as pdf:
        problems = pdf.check_pdf_syntax()
    assert problems == [], f"{template_name}: qpdf/pikepdf check_pdf_syntax: {problems}"

    cloned = PdfWriter(clone_from=PdfReader(output))
    buffer = BytesIO()
    cloned.write(buffer)
    assert buffer.getvalue().startswith(b"%PDF")
    assert len(cloned.pages) == source_pages
