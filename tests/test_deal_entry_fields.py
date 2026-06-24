"""Deal-model entry fields from Mark Willcott's contract markup (2026-06-24).

The Document Center *form* already captures these (phone/SSN masks, co-buyer DOB,
employment unit, loan number, marital dropdown — shipped earlier). But the
DEAL-BASED generation path goes Deal -> Deal.to_document_data() -> engine, and
the Deal model was missing buyer_dob / co_buyer_dob / loan_number, so a document
generated FROM A SAVED DEAL silently dropped them. These map to real PDF widgets:

  buyer_dob   -> creditapp.pdf Buyer_DOB, Internal_Homestead.pdf Buyer_DOB,
                 State_PatriotAct.pdf THO_PatriotAct_Buyer_DOB   (PII)
  co_buyer_dob-> creditapp.pdf CoBuyer_DOB, State_PatriotAct THO_PatriotAct_CoBuyer_DOB
  loan_number -> Portfolio[0] on the Compliance Agreement + two-party contract
                 (enrich_document_data backfills portfolio from loan_number)

This pins that the deal path carries them. No PII is logged here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Deal  # noqa: E402


def test_deal_carries_buyer_and_co_buyer_dob():
    deal = Deal(buyer_dob="1986-04-12", co_buyer_dob="1988-09-30")
    data = deal.to_document_data()
    assert data["buyer_dob"] == "1986-04-12"
    assert data["co_buyer_dob"] == "1988-09-30"


def test_deal_carries_loan_number():
    deal = Deal(loan_number="LN-100245")
    data = deal.to_document_data()
    assert data["loan_number"] == "LN-100245"


def test_loan_number_backfills_compliance_agreement_portfolio():
    """loan_number must reach the Compliance Agreement Portfolio[0] line.

    enrich_document_data backfills portfolio from loan_number; this proves the
    deal-based path supplies loan_number so that wiring fires (punch-list #9).
    """
    from tools.document_quality import enrich_document_data

    deal = Deal(
        buyer_first_name="Jordan",
        buyer_last_name="Brooks",
        loan_number="LN-100245",
    )
    enriched = enrich_document_data(deal.to_document_data())
    assert enriched.get("portfolio") == "LN-100245"


def test_dob_fields_default_to_none():
    deal = Deal()
    data = deal.to_document_data()
    # Present in the document payload (so templates can map them) but empty.
    assert data.get("buyer_dob") is None
    assert data.get("co_buyer_dob") is None
    assert data.get("loan_number") is None
