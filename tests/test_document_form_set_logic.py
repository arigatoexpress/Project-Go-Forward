"""Phase 3 form-set logic tests (Mark Willcott acceptance review).

Covers C1/C2/C3/C5 from the hand-marked 65-page closing-packet review:
  C1 — de-dupe "Need to Vacate Home" -> one copy.
  C2 — de-dupe "Arbitration Agreement" -> one copy (keep the Internal copy).
  C3 — suppress USED-home-only forms on NEW-home packages (gate on condition).
  C5 — gate the TMHA R020 "MH Sales Contract & Deposit Agreement" behind a
       config flag, DEFAULT-EXCLUDED from the standard NEW closing packet.
  C4 — de-dupe the retailer compliance checklist (Celeste 2026-07-03): keep
       the 1058 Compliance Review (Rev. 10/2024), drop the 2013 retmonlist.

The static packet lists in field_map.json remain the superset; these rules are
applied by tools.document_engine_v2.resolve_form_set() at packet/batch time.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.field_map_loader import get_form_set_rules
from tools.document_engine_v2 import (
    preview_packet_templates,
    resolve_form_set,
)

# Same-document templates that must collapse to one copy.
ARBITRATION_INTERNAL = "Internal_Arbitration_Agreement11.pdf"
ARBITRATION_TDHCA = "TDHCA_ArbitrationAgreement.pdf"
VACATE = "TDHCA_1074_Vacate_If_No_Financing.pdf"
COMPLIANCE_1058 = "TDHCA_1058_Compliance_Review.pdf"
RETMONLIST_2013 = "TDHCA-retmonlist.pdf"

# USED-home-only forms (C3).
USED_ONLY_FORMS = ("TDHCA-ImproperSite.pdf", "TDHCA_SitePreparation.pdf")

# Flag-gated optional (C5).
R020_DEPOSIT = "TMHA-SalesContractDepositAgreement.pdf"

# The standard NEW closing packet Mark reviewed.
NEW_PACKET = "full_closing_new"
USED_PACKET = "full_closing_used"

NEW_HOME_DATA = {"condition": "New"}
USED_HOME_DATA = {"condition": "Used"}


# ── Config presence ──────────────────────────────────────────────────────────


def test_form_set_rules_block_exists():
    rules = get_form_set_rules()
    assert rules, "form_set_rules block missing from field_map.json"
    assert rules.get("duplicate_groups"), "duplicate_groups not configured"
    assert rules.get("used_home_only_templates"), "used_home_only_templates not configured"
    assert rules.get("flag_gated_templates"), "flag_gated_templates not configured"


# ── C2: Arbitration de-dupe ──────────────────────────────────────────────────


def test_c2_arbitration_deduped_to_one_copy():
    out = resolve_form_set([ARBITRATION_INTERNAL, ARBITRATION_TDHCA], NEW_HOME_DATA)
    assert ARBITRATION_INTERNAL in out, "Internal arbitration copy must be kept"
    assert ARBITRATION_TDHCA not in out, "TDHCA arbitration duplicate must be dropped"
    assert out.count(ARBITRATION_INTERNAL) == 1


def test_c2_arbitration_deduped_in_new_packet():
    new_pkt = preview_packet_templates(NEW_PACKET, NEW_HOME_DATA)
    assert ARBITRATION_INTERNAL in new_pkt
    assert ARBITRATION_TDHCA not in new_pkt


def test_c2_arbitration_deduped_even_on_used_packet():
    # The buyer signs ONE arbitration agreement regardless of new/used.
    used_pkt = preview_packet_templates(USED_PACKET, USED_HOME_DATA)
    assert ARBITRATION_INTERNAL in used_pkt
    assert ARBITRATION_TDHCA not in used_pkt


def test_c2_keeps_first_present_when_only_tdhca_supplied():
    # If only the TDHCA copy is present, it must still flow through (no copy lost).
    out = resolve_form_set(["TMHA_SalesContract.pdf", ARBITRATION_TDHCA], NEW_HOME_DATA)
    assert ARBITRATION_TDHCA in out


# ── C1: Vacate de-dupe ───────────────────────────────────────────────────────


def test_c1_vacate_single_copy_in_new_packet():
    new_pkt = preview_packet_templates(NEW_PACKET, NEW_HOME_DATA)
    assert new_pkt.count(VACATE) == 1, "Vacate-Home form must appear exactly once"


def test_c1_exact_duplicate_template_collapsed():
    # Safety net: an accidental double-listing collapses to one.
    out = resolve_form_set([VACATE, "TMHA_SalesContract.pdf", VACATE], NEW_HOME_DATA)
    assert out.count(VACATE) == 1


# ── C4: Retailer compliance checklist de-dupe (Celeste 2026-07-03) ───────────


def test_c4_compliance_checklist_deduped_to_most_recent():
    out = resolve_form_set([RETMONLIST_2013, COMPLIANCE_1058], NEW_HOME_DATA)
    assert COMPLIANCE_1058 in out, "1058 Compliance Review (Rev. 10/2024) must be kept"
    assert RETMONLIST_2013 not in out, "2013 retmonlist duplicate must be dropped"
    assert out.count(COMPLIANCE_1058) == 1


def test_c4_compliance_checklist_deduped_in_new_packet():
    new_pkt = preview_packet_templates(NEW_PACKET, NEW_HOME_DATA)
    assert COMPLIANCE_1058 in new_pkt
    assert RETMONLIST_2013 not in new_pkt


def test_c4_compliance_checklist_deduped_in_used_packet():
    used_pkt = preview_packet_templates(USED_PACKET, USED_HOME_DATA)
    assert COMPLIANCE_1058 in used_pkt
    assert RETMONLIST_2013 not in used_pkt


def test_c4_retmonlist_flows_through_when_alone():
    # If a packet only carries the older checklist, it must not be lost.
    out = resolve_form_set(["TMHA_SalesContract.pdf", RETMONLIST_2013], NEW_HOME_DATA)
    assert RETMONLIST_2013 in out


# ── C3: USED-home-only suppression on NEW packages ───────────────────────────


def test_c3_used_only_forms_suppressed_on_new_packet():
    new_pkt = preview_packet_templates(NEW_PACKET, NEW_HOME_DATA)
    for form in USED_ONLY_FORMS:
        assert form not in new_pkt, f"{form} is USED-home-only and must be suppressed on NEW"


def test_c3_used_only_forms_kept_on_used_packet():
    used_pkt = preview_packet_templates(USED_PACKET, USED_HOME_DATA)
    for form in USED_ONLY_FORMS:
        assert form in used_pkt, f"{form} must remain on the USED-home packet"


def test_c3_condition_resolution_variants_new():
    # NEW resolved from condition string, is_new flag, and is_used flag.
    for data in ({"condition": "New"}, {"is_new": True}, {"is_used": False}, {}):
        out = resolve_form_set(list(USED_ONLY_FORMS), data)
        assert out == [], f"used-only forms should be dropped for NEW signal {data}"


def test_c3_condition_resolution_variants_used():
    for data in ({"condition": "Used"}, {"is_new": False}, {"is_used": True}):
        out = resolve_form_set(list(USED_ONLY_FORMS), data)
        assert set(out) == set(USED_ONLY_FORMS), f"used-only forms kept for USED signal {data}"


# ── C5: R020 deposit agreement gated OFF by default ──────────────────────────


def test_c5_r020_excluded_from_new_packet_by_default():
    new_pkt = preview_packet_templates(NEW_PACKET, NEW_HOME_DATA)
    assert R020_DEPOSIT not in new_pkt, "R020 must be excluded from the standard NEW packet"


def test_c5_r020_included_when_flag_set():
    out = resolve_form_set([R020_DEPOSIT, "TMHA_SalesContract.pdf"], {"include_r020": True})
    assert R020_DEPOSIT in out


def test_c5_r020_included_via_form_set_flags_dict():
    out = resolve_form_set([R020_DEPOSIT], {"form_set_flags": {"include_r020": True}})
    assert R020_DEPOSIT in out


def test_c5_r020_excluded_by_default():
    out = resolve_form_set([R020_DEPOSIT, "TMHA_SalesContract.pdf"], {})
    assert R020_DEPOSIT not in out
    assert "TMHA_SalesContract.pdf" in out


# ── Combined invariants for the standard NEW closing packet ──────────────────


def test_new_packet_is_clean_overall():
    new_pkt = preview_packet_templates(NEW_PACKET, NEW_HOME_DATA)
    # No exact duplicates.
    assert len(new_pkt) == len(set(new_pkt))
    # None of the suppressed / gated forms present.
    for form in (ARBITRATION_TDHCA, *USED_ONLY_FORMS, R020_DEPOSIT, RETMONLIST_2013):
        assert form not in new_pkt
    # Core closing documents still present.
    for form in ("TMHA_SalesContract.pdf", ARBITRATION_INTERNAL, VACATE, COMPLIANCE_1058):
        assert form in new_pkt


def test_resolve_preserves_order_of_untouched_templates():
    templates = ["TMHA_SalesContract.pdf", "TDHCA_1038_Consumer_Disclosure.pdf", VACATE]
    out = resolve_form_set(templates, NEW_HOME_DATA)
    assert out == templates


def test_unknown_packet_returns_empty():
    assert preview_packet_templates("does_not_exist", NEW_HOME_DATA) == []
