"""Tests for DocumentCenter Step 2 inventory auto-fill wiring.

This is a static-source test: the React component is uncontrolled (`defaultValue`
+ `key={name + '-' + resetKey}`) so writing form state alone won't update the
visible input — the resetKey must be bumped when handleSelectHome runs. These
checks guarantee the wiring stays intact.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_CENTER = REPO_ROOT / "frontend" / "src" / "pages" / "DocumentCenter.jsx"


@pytest.fixture(scope="module")
def source() -> str:
    assert DOC_CENTER.exists(), f"Missing component file: {DOC_CENTER}"
    return DOC_CENTER.read_text()


def test_step2_exposes_onautofill_prop(source: str):
    """Step2 must accept the onAutoFill callback so it can drive the
    parent's resetKey-bump path on home selection."""
    assert "onAutoFill" in source
    assert "function Step2(" in source
    # Every Step2 invocation in the parent component should now thread
    # onAutoFill down (currently only one site, but guard it).
    invocation_count = source.count("onAutoFill={applyInventoryAutoFill}")
    assert invocation_count >= 1, "Step2 should receive applyInventoryAutoFill"


def test_apply_inventory_autofill_bumps_reset_key(source: str):
    """The auto-fill handler MUST bump formResetKey — without that, the
    uncontrolled <input>s keep their stale defaultValue and the user
    sees the card highlight but no field text."""
    # Find applyInventoryAutoFill body
    start = source.find("const applyInventoryAutoFill")
    assert start != -1, "applyInventoryAutoFill not defined"
    end = source.find("}, []);", start)
    assert end != -1, "applyInventoryAutoFill body not closed"
    body = source[start:end]
    assert "setForm" in body
    assert "setFormResetKey" in body, (
        "applyInventoryAutoFill must bump formResetKey or Field inputs "
        "won't refresh on home selection"
    )
    assert "setAutoFilledFields" in body


def test_handleselecthome_uses_onautofill(source: str):
    """Step2.handleSelectHome should route through onAutoFill (single
    batched patch) rather than firing a per-key sequence of c() calls
    that don't visually refresh the inputs."""
    start = source.find("const handleSelectHome = (home)")
    assert start != -1
    # Slice up to the next sibling declaration to capture the full body
    end = source.find("const handleClearSelection", start)
    assert end != -1
    body = source[start:end]
    assert "onAutoFill" in body, "handleSelectHome must call onAutoFill"
    assert "patch" in body, "handleSelectHome should build a patch object"
    for key in ("manufacturer", "model", "year", "no_of_sections"):
        assert key in body, f"handleSelectHome should populate {key}"


def test_handleselecthome_populates_factory_document_fields(source: str):
    """Home selection should carry the document-critical factory fields when
    the admin inventory API provides them."""
    start = source.find("const handleSelectHome = (home)")
    end = source.find("const handleClearSelection", start)
    body = source[start:end]
    for key in (
        "wind_zone",
        "serial_number_2",
        "label_number_2",
        "weight_sec_1",
        "weight_sec_2",
        "date_of_manufacture",
        "manufacturer_address",
        "manufacturer_city",
        "manufacturer_state",
        "manufacturer_zip",
    ):
        assert key in body, f"handleSelectHome should populate {key}"


def test_document_center_has_installer_default_and_alternate_path(source: str):
    """THO should be the default installer, while allowing another installer
    for deals that need it."""
    assert "installer_type: 'tho'" in source
    assert "Texas Home Outlet installs this home" in source
    assert "Use another installer" in source
    assert "handleInstallerChoice" in source
    assert "installer_name_address" in source
    assert "installer_address_city_state_zip" in source


def test_document_center_uses_shared_tho_business_constants(source: str):
    """Installer defaults should use the same business constants as the rest
    of the site instead of copy-pasted THO identity text."""
    assert "BUSINESS_NAME" in source
    assert "BUSINESS_PHONE" in source
    assert "BUSINESS_ADDRESS" in source
    assert "BUSINESS_ZIP" in source


def test_field_renders_autofilled_badge(source: str):
    """Field component must render a 'from inventory' badge when autoFilled."""
    field_start = source.find("const Field = React.memo")
    assert field_start != -1
    field_end = source.find("});", field_start)
    field_body = source[field_start:field_end]
    assert "autoFilled" in field_body, "Field must accept autoFilled prop"
    assert "from inventory" in field_body, "Field must render the 'from inventory' confirmation tag"


def test_field_renders_helper_text(source: str):
    """Field must render helperText so we can show "Enter manually — not in
    inventory feed" on serial/label/sales_price fields."""
    field_start = source.find("const Field = React.memo")
    field_end = source.find("});", field_start)
    field_body = source[field_start:field_end]
    assert "helperText" in field_body
    assert "data-testid" in field_body and "helper-" in field_body


def test_serial_number_field_has_manual_helper(source: str):
    """Serial # 1 must show the manual-entry hint when a home is picked
    but the inventory feed didn't supply a serial — the texashomeoutlet.com
    feed always returns serial_number=null."""
    # The exact wiring lives in Step2's JSX
    assert (
        'name="serial_number_1"' in source
        and "Enter manually" in source
        and "not in inventory feed" in source
    ), "Serial #1 must show 'Enter manually — not in inventory feed' helper"


def test_inventory_cards_do_not_render_zero_sale_price(source: str):
    """The inventory feed can send sale_price=0.0. Document Center should
    omit that from the card instead of showing reps a fake $0 price."""
    assert "formatPositiveCurrency" in source
    assert "const salePriceLabel = formatPositiveCurrency(home.sale_price)" in source
    assert "{salePriceLabel && (" in source
    assert "{home.sale_price && (" not in source
    assert "${Number(home.sale_price).toLocaleString()}" not in source


def test_validation_state_unchanged_for_step2(source: str):
    """Existing validator pattern must be preserved — Continue to
    Documents stays gated on Serial #1 even with auto-fill in place."""
    # The validator block itself is the canonical gate
    assert "if (step === 2) {" in source
    assert "Serial # 1 is required" in source
    assert "form.serial_number_1?.trim()" in source


def test_selected_template_required_fields_gate_generation(source: str):
    """Selected PDFs can require more than the generic Step 2 fields. The
    UI should catch those template-specific gaps before calling the backend."""
    assert "function getSelectedTemplateRequiredState" in source
    assert "template?.required_fields" in source
    assert "getSelectedTemplateRequiredState(form, templates, selDocs)" in source
    assert "setStep(selectedRequired.step)" in source
    assert "Installation street address" in source
    assert "Sales price" in source


def test_document_center_blocks_placeholder_identifiers_and_unmapped_note_templates(source: str):
    """UI validation should stop bad packets before the backend call."""
    assert "PLACEHOLDER_IDENTIFIER_VALUES" in source
    assert "isPlaceholderIdentifier(form.serial_number_1)" in source
    assert "isPlaceholderIdentifier(form.label_number_1)" in source
    assert "PRODUCTION_BLOCKED_TEMPLATE_MESSAGES" in source
    assert "TMHA-TwoPartyContract.pdf" in source
    assert "getDocumentQualityState(form, selDocs)" in source
    assert "quality_issues" in source


def test_document_center_adds_seller_and_financing_aliases(source: str):
    """Generated data should carry THO seller defaults and deterministic
    finance aliases so mapped PDFs do not leave avoidable blank fields."""
    data_start = source.find("function toDocumentData")
    data_body = source[data_start : source.find("function formatBytes", data_start)]
    assert "seller_name: f.seller_name || BUSINESS_NAME" in data_body
    assert "seller_address: f.seller_address || BUSINESS_ADDRESS" in data_body
    assert "max_financed:" in data_body
    assert "unpaid_balance:" in data_body
    assert "interest_rate:" in data_body


def test_step3_waits_for_template_metadata_before_generation(source: str):
    """A restored draft on Step 3 must not generate before template metadata
    is loaded, because required_fields live in /api/documents/templates."""
    assert "const [templatesLoading, setTemplatesLoading]" in source
    assert "setTemplatesLoading(true)" in source
    assert "Document templates are still loading" in source
    assert "disabled={templatesLoading || selected.length === 0}" in source
    assert "Selected document is no longer available" in source


def test_selected_template_missing_fields_highlight_step2_inputs(source: str):
    """Template-required installation and pricing fields should get the same
    inline highlight treatment as manufacturer/model/serial."""
    for field in (
        "error={errOf('buyer_address')}",
        "error={errOf('buyer_city')}",
        "error={errOf('buyer_county')}",
        "error={errOf('buyer_state')}",
        "error={errOf('buyer_zip')}",
        "error={errOf('sales_price')}",
    ):
        assert field in source


def test_chg_clears_autofill_marker_on_manual_edit(source: str):
    """When the user manually edits a field, the auto-fill badge must drop
    so the user understands they own the value now."""
    chg_start = source.find("const chg = useCallback")
    assert chg_start != -1
    chg_end = source.find("}, [checkForDuplicates]);", chg_start)
    chg_body = source[chg_start:chg_end]
    assert "setAutoFilledFields" in chg_body, (
        "chg() must clear the auto-fill marker when the user types over a "
        "previously auto-filled value"
    )


def test_manual_customer_save_payload_is_fuller_and_masked(source: str):
    """Manual customer entry should persist the useful customer fields without
    shipping raw SSNs to the customer API."""
    assert "function buildCustomerPayload" in source
    assert "legacy_source: 'manual'" in source
    assert "customer_status" in source
    assert "customer_notes" in source
    assert "co_buyer:" in source
    assert "maskSsn(data.buyer_ssn)" in source
    assert "maskSsn(data.co_buyer_ssn)" in source
    payload_start = source.find("function buildCustomerPayload")
    payload_body = source[payload_start : source.find("function safeDraftForm", payload_start)]
    assert "buyer_ssn:" not in payload_body


def test_document_center_draft_storage_masks_ssn_fields(source: str):
    """Local draft autosave should not persist raw SSNs in browser storage."""
    assert "function safeDraftForm" in source
    safe_start = source.find("function safeDraftForm")
    safe_body = source[safe_start : source.find("function BigButton", safe_start)]
    assert "buyer_ssn: maskSsn(data.buyer_ssn)" in safe_body
    assert "co_buyer_ssn: maskSsn(data.co_buyer_ssn)" in safe_body
    assert "form: safeDraftForm(form)" in source


def test_save_customer_record_button_is_visible_before_step_validation(source: str):
    """The customer-save action should be visible during manual entry, even
    before the full Step 1 validation passes."""
    assert "Save Customer Record" in source
    assert "canSaveCustomer" in source
    assert "disabled={!canSaveCustomer || savingCustomer}" in source
    assert "Enter buyer first and last name to save a customer record." in source


def test_document_center_navigation_appears_before_status_desk(source: str):
    """A restored draft may land on Pick Documents or Review & Generate, so
    step shortcuts must be visible before the heavier document-status panels."""
    workflow_index = source.find("<WorkflowShortcuts")
    desk_index = source.find("<DocumentDesk")
    stepbar_index = source.find("<StepBar")
    assert workflow_index != -1, "Document Center should render workflow shortcuts"
    assert desk_index != -1, "Document Center should render document desk status"
    assert stepbar_index != -1, "Document Center should render the step bar"
    assert workflow_index < stepbar_index < desk_index, (
        "navigation should come before status/recent-PDF panels so admins can "
        "escape a restored later step immediately"
    )


def test_document_desk_metric_tiles_do_not_force_four_columns(source: str):
    """Production counts can be four digits. The status card should keep a
    roomy two-column metric grid instead of viewport-driven four columns that
    squeeze inside a narrow card."""
    start = source.find("function DocumentDesk")
    assert start != -1
    body = source[start : source.find("/* ─── Duplicate Warning", start)]
    assert "grid grid-cols-2 gap-3" in body
    assert "sm:grid-cols-4" not in body
    assert "break-words" in body
