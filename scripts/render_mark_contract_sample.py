#!/usr/bin/env python3
"""Render fake-buyer TMHA sales contracts for the 2026-10-26 review.

Writes two PDFs under data/generated_docs/mark-1026-sample/:

  mark-1026-customer-paid.pdf
      Jordan Brooks, 21st Mortgage. Annual premium $2,400 paid by the customer.
      Page 1 cash price stays $87,000. Page 2 payment is Mark's sample
      $1,251.26. Page 3 fills the "you have paid" line.

  mark-1026-financed-prosperity.pdf
      Same home and price, first year financed, in-house Prosperity creditor.
      Cash price on the contract is $89,400. The creditor address stays blank
      because lenders.prosperity.autofill_enabled is false until Mark confirms it.

Buyer, home, and serials are fictional. Do not put real customer data here.
The regulatory template in tho_documents/ is not modified.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("THO_DISABLE_GCS_UPLOADS", "1")

import tools.document_tools as document_tools  # noqa: E402
from tools.document_engine_v2 import generate_document  # noqa: E402

CONTRACT = "TMHA_SalesContract.pdf"
SAMPLE_DIR = Path("data/generated_docs/mark-1026-sample")

# Fictional buyer already used by tests/test_escrow_printed_payment.py.
# Financial figures are Mark's sample deal, with a $2,400 annual premium
# (the $200 monthly escrow).
_BASE: dict[str, object] = {
    "buyer_first_name": "Jordan",
    "buyer_last_name": "Brooks",
    "buyer_address": "456 Meadow Lane",
    "buyer_city": "Austin",
    "buyer_county": "Travis",
    "buyer_state": "TX",
    "buyer_zip": "78701",
    "is_new": True,
    "manufacturer": "TRU Homes",
    "model": "The Marvel",
    "year": "2026",
    "serial_number_1": "TRU0987654A",
    "serial_number_2": "TRU0987654B",
    "label_number_1": "TEX0482913",
    "label_number_2": "TEX0482914",
    "no_of_sections": "Double Section",
    "date_of_manufacture": "2026-01-15",
    "manufacturer_address": "500 Factory Road",
    "manufacturer_city": "Fort Worth",
    "manufacturer_state": "TX",
    "manufacturer_zip": "76101",
    "payment_start_date": "2026-11-01",
    "sales_price": "87000",
    "down_payment": "10000",
    "apr": "12.84",
    "loan_term": "276",
    "tax_rate": "2.5",
    "insurance_annual_premium": "2400",
}


def customer_paid_sample() -> dict[str, object]:
    return {
        **_BASE,
        "insurance_first_year_paid_by": "customer",
        "creditor_name": "21st Mortgage",
    }


def financed_prosperity_sample() -> dict[str, object]:
    return {
        **_BASE,
        "insurance_first_year_paid_by": "financed",
        "creditor_name": "Prosperity Acquisitions, Inc. dba Texas Home Outlet",
    }


def write_review_pdfs(directory: Path | None = None) -> list[Path]:
    """Generate both review PDFs into ``directory`` and return their paths."""
    target = Path(directory or SAMPLE_DIR)
    target.mkdir(parents=True, exist_ok=True)
    document_tools.OUTPUT_DIR = str(target)
    written: list[Path] = []
    samples = (
        ("mark-1026-customer-paid.pdf", customer_paid_sample()),
        ("mark-1026-financed-prosperity.pdf", financed_prosperity_sample()),
    )
    for filename, data in samples:
        result = generate_document(CONTRACT, dict(data), output_filename=filename)
        if not result.get("success"):
            raise RuntimeError(result.get("message") or f"failed to generate {filename}")
        written.append(Path(result["file_path"]))
    return written


def main() -> None:
    for path in write_review_pdfs():
        print(path)


if __name__ == "__main__":
    main()
