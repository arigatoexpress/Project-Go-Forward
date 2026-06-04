#!/usr/bin/env python3
"""Strong end-to-end validation of THO document generation.

Targets the exact bug clients hit: documents that generate but leave required
information BLANK — sometimes deep in a 50+ page closing packet.

THO templates are filled by TWO mechanisms (see tools/document_tools.py):
AcroForm field values with baked /AP appearances AND direct text overlays.
``pypdf.extract_text()`` only reliably sees one of them, so this validator uses
``pdftotext`` (poppler) as ground truth — it reads what a PDF viewer actually
renders, which is what the client sees.

It generates every packet through the real backend engine, then asserts:

  1. REQUIRED-FIELD RENDER: every template ``required_field`` value actually
     renders in the generated PDF (not just stored in the data layer).
  2. MERGE SURVIVAL: every distinctive value that renders in a standalone
     document still renders in the merged packet — this is the "pages 40+ went
     blank" regression, where AcroForm values are dropped during page merge.
  3. NO BLANK PAGES in the merged packet.

Run:  python3 scripts/validate_document_fills.py                 # all packets
      python3 scripts/validate_document_fills.py --packet full_closing_new
      python3 scripts/validate_document_fills.py --json

Exit 0 = clean; 1 = blanks/missing renders found; 2 = generation error.
Requires poppler's ``pdftotext`` on PATH (apt-get install poppler-utils).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.field_map_loader import get_field_map  # noqa: E402
from tools.document_engine_v2 import generate_batch, generate_document  # noqa: E402
from tools.document_quality import enrich_document_data  # noqa: E402
from tools.document_tools import OUTPUT_DIR  # noqa: E402

# Complete, realistic Document Center submission (buyer + co-buyer, full home
# spec, full financing). Realistic on purpose — the quality gate rejects
# "Sample"/"Test" values. installer_* are auto-filled from business constants by
# enrich_document_data, mirroring the frontend default (installer = THO).
FULL_DEAL: dict[str, object] = {
    "buyer_first_name": "Jordan", "buyer_last_name": "Brooks",
    "buyer_name": "Jordan Avery Brooks",
    "buyer_address": "456 Meadow Lane", "buyer_city": "Austin",
    "buyer_county": "Travis", "buyer_state": "TX", "buyer_zip": "78701",
    "buyer_phone": "512-555-0123", "buyer_email": "jordan.brooks@example.com",
    "buyer_ssn": "456-12-7890", "buyer_dob": "1986-04-12",
    "buyer_marital_status": "Married", "buyer_income": "78000",
    "co_buyer_first_name": "Taylor", "co_buyer_last_name": "Brooks",
    "co_buyer_name": "Taylor Morgan Brooks", "co_buyer_phone": "512-555-0144",
    "co_buyer_ssn": "457-22-3344", "co_buyer_dob": "1988-09-30",
    "employer_name": "Lone Star Logistics", "occupation": "Operations Manager",
    "occupation_length": "6 years", "work_phone": "512-555-0199",
    "co_buyer_employer": "Austin ISD", "co_buyer_occupation": "Teacher",
    "co_buyer_occupation_length": "4 years", "co_buyer_work_phone": "512-555-0166",
    "mailing_address": "456 Meadow Lane", "mailing_city": "Austin",
    "mailing_state": "TX", "mailing_zip": "78701", "mailing_length": "3 years",
    "mailing_own_rent": "Rent",
    "reference1_name": "Pat Rivera", "reference1_phone": "512-555-0177",
    "reference2_name": "Sam Delgado", "reference2_phone": "512-555-0188",
    "is_new": True, "is_used": False,
    "manufacturer": "TRU Homes", "model": "The Marvel",
    "manufacturer_model": "TRU Homes The Marvel", "year": "2026",
    "serial_number_1": "TRU0987654A", "serial_number_2": "TRU0987654B",
    "label_number_1": "TEX0482913", "label_number_2": "TEX0482914",
    "no_of_sections": "Double Section", "width": "32", "length": "76",
    "sq_ft": "2128", "wind_zone": "2",
    "weight_sec_1": "21000", "weight_sec_2": "21500",
    "date_of_manufacture": "2026-01-15",
    "manufacturer_address": "500 Factory Road", "manufacturer_city": "Fort Worth",
    "manufacturer_state": "TX", "manufacturer_zip": "76101",
    "sales_price": "95000", "down_payment": "5000", "loan_term": "240",
    "apr": "8.5", "doc_fee": "150", "insurance_premium": "1200",
    "payment_start_date": "2026-08-01", "date_of_sale": "2026-06-04",
    "creditor_name": "21st Mortgage Corporation",
    "creditor_address": "620 Market Street",
    "creditor_city_state_zip": "Knoxville, TN 37902",
    "creditor_phone": "865-555-0100",
    "salesrep": "Lee Carter",
    "notes": "Buyer financing approved; install on owned lot in Travis County.",
}

# Distinctive values used to prove a fill survives the packet merge. Each must
# be long/unique enough that an incidental match is implausible.
SURVIVAL_PROBE_KEYS = [
    "buyer_address", "buyer_city", "buyer_county", "manufacturer", "model",
    "serial_number_1", "serial_number_2", "label_number_1", "label_number_2",
    "creditor_name", "sales_price", "manufacturer_address",
]

_NUM_RE = re.compile(r"^\$?-?[\d,]+(\.\d+)?$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def value_candidates(value: object) -> list[str]:
    """Render variants to search for (money gets comma-grouped + .00)."""
    s = str(value).strip()
    if not s:
        return []
    cands = {s}
    digits = s.replace("$", "").replace(",", "").strip()
    if _NUM_RE.match(s) and digits.replace(".", "").isdigit():
        try:
            num = float(digits)
            if num == int(num):
                cands.add(f"{int(num):,}")
                cands.add(f"{int(num):,}.00")
            cands.add(f"{num:,.2f}")
        except ValueError:
            pass
    return [_norm(c) for c in cands if c]


def render_text(pdf_path: str) -> str:
    # -raw emits text in content-stream order, which keeps multi-word field
    # values contiguous. -layout reflows into visual columns and can split a
    # field value across the page, producing false "did not render" matches.
    out = subprocess.run(
        ["pdftotext", "-raw", pdf_path, "-"],
        check=True, capture_output=True, text=True,
    ).stdout
    return _norm(out)


def _renders(text: str, value: object) -> bool:
    return any(c in text for c in value_candidates(value))


def _page_has_content(page) -> bool:
    try:
        txt = (page.extract_text() or "").strip()
    except Exception:
        txt = ""
    if len(txt) >= 15:
        return True
    res = page.get("/Resources")
    if res is not None:
        res = res.get_object()
        xo = res.get("/XObject")
        if xo is not None and len(xo.get_object()):
            return True
    try:
        data = page.get_contents()
        if data is not None and len(data.get_data()) > 80:
            return True
    except Exception:
        pass
    for annot in page.get("/Annots") or []:
        obj = annot.get_object()
        if obj.get("/Subtype") == "/Widget" and obj.get("/V") not in (None, "") and "/AP" in obj:
            return True
    return False


def validate_packet(packet_name: str, fmap: dict) -> dict:
    templates = fmap["packets"][packet_name]["templates"]
    enriched = enrich_document_data(dict(FULL_DEAL))

    required_failures: list[dict] = []
    rendered_probes: set[str] = set()       # probe keys that rendered somewhere
    gen_errors: list[dict] = []

    for tpl in templates:
        cfg = fmap["templates"].get(tpl, {})
        if not cfg.get("field_map"):
            continue  # static disclosure page, nothing to fill
        res = generate_document(tpl, dict(FULL_DEAL))
        if not res.get("success") or not res.get("file_path"):
            gen_errors.append({"template": tpl, "error": res.get("message", "generation failed")})
            continue
        text = render_text(res["file_path"])

        # (1) Required-field render check.
        missing = []
        for key in cfg.get("required_fields", []) or []:
            val = enriched.get(key)
            if val is None or str(val).strip() == "":
                missing.append({"field": key, "reason": "no input value"})
            elif not _renders(text, val):
                missing.append({"field": key, "reason": "value did not render", "value": str(val)})
        if missing:
            required_failures.append({"template": tpl, "missing": missing})

        # Track which distinctive probes render in this standalone doc.
        for key in SURVIVAL_PROBE_KEYS:
            if key in cfg.get("field_map", {}).values() and _renders(text, enriched.get(key, "")):
                rendered_probes.add(key)

    # (2) + (3): merged packet survival + blank pages.
    batch = generate_batch(templates, dict(FULL_DEAL), merge=True)
    merged_pages = merged_blanks = merged_dropped = None
    if batch.get("success"):
        cands = sorted(glob.glob(os.path.join(OUTPUT_DIR, "Documents_*.pdf")), key=os.path.getmtime)
        if cands:
            merged_path = cands[-1]
            reader = PdfReader(merged_path)
            merged_pages = len(reader.pages)
            merged_blanks = [i for i, p in enumerate(reader.pages, 1) if not _page_has_content(p)]
            mtext = render_text(merged_path)
            # A probe that rendered standalone but is now absent = merge dropped it.
            merged_dropped = [
                k for k in sorted(rendered_probes) if not _renders(mtext, enriched.get(k, ""))
            ]

    ok = (
        batch.get("success", False)
        and not gen_errors
        and not required_failures
        and not merged_blanks
        and not merged_dropped
    )
    return {
        "packet": packet_name,
        "ok": ok,
        "fillable_docs": sum(
            1 for t in templates if fmap["templates"].get(t, {}).get("field_map")
        ),
        "generation_errors": gen_errors,
        "required_field_failures": required_failures,
        "probes_rendered": sorted(rendered_probes),
        "merged_pages": merged_pages,
        "merged_blank_pages": merged_blanks,
        "merge_dropped_values": merged_dropped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", action="append", help="Packet name(s); default = all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not shutil.which("pdftotext"):
        print("ERROR: pdftotext not found. Install poppler-utils.", file=sys.stderr)
        return 2

    fmap = get_field_map()
    packets = args.packet or list(fmap["packets"].keys())
    results = [validate_packet(p, fmap) for p in packets]
    overall_ok = all(r["ok"] for r in results)

    if args.json:
        print(json.dumps({"ok": overall_ok, "packets": results}, indent=2, default=str))
        return 0 if overall_ok else 1

    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"\n[{status}] {r['packet']}  "
              f"({r['fillable_docs']} fillable docs, {r['merged_pages']} merged pages, "
              f"{len(r['probes_rendered'])} probes rendered)")
        for e in r["generation_errors"]:
            print(f"   ! GENERATION FAILED {e['template']}: {e['error']}")
        for f in r["required_field_failures"]:
            for m in f["missing"]:
                val = f" ('{m.get('value')}')" if m.get("value") else ""
                print(f"   ! REQUIRED BLANK  {f['template']}: {m['field']} — {m['reason']}{val}")
        if r["merged_blank_pages"]:
            print(f"   ! BLANK PAGES in merged packet: {r['merged_blank_pages']}")
        if r["merge_dropped_values"]:
            print(f"   ! MERGE DROPPED values that rendered standalone: {r['merge_dropped_values']}")

    clean = sum(1 for r in results if r["ok"])
    print(f"\n{'=' * 60}\nOVERALL: {'PASS' if overall_ok else 'FAIL'}  ({clean}/{len(results)} packets clean)")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
