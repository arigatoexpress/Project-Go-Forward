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
    assert "normalizeSections(home.sections)" in body


def test_sections_are_required_and_normalized_for_documents(source: str):
    assert "const normalizeSections" in source
    assert "field: 'no_of_sections', label: '# of Sections', step: 2" in source
    assert "# of Sections is required" in source
    assert "no_of_sections: normalizeSections(f.no_of_sections)" in source


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
    assert "BUSINESS_LEGAL_NAME" in source
    assert "BUSINESS_PHONE" in source
    assert "BUSINESS_ADDRESS" in source
    assert "BUSINESS_ZIP" in source
    assert "BUSINESS_LICENSE" in source
    assert "seller_rbi: BUSINESS_LICENSE" in source
    assert "seller_rbi: f.seller_rbi || BUSINESS_LICENSE" in source


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
    assert "seller_name: f.seller_name || BUSINESS_LEGAL_NAME" in data_body
    assert "seller_address: f.seller_address || BUSINESS_ADDRESS" in data_body
    assert "max_financed:" in data_body
    assert "unpaid_balance:" in data_body
    assert "interest_rate:" in data_body


def test_document_center_defaults_to_tho_legal_entity_for_documents(source: str):
    """Documents should use THO's legal entity name, not just the public
    retail brand, when defaulting seller/installer fields."""
    assert 'BUSINESS_LEGAL_NAME = "Prosperity Acquisitions, Inc. dba Texas Home Outlet"' in (
        (REPO_ROOT / "frontend" / "src" / "constants.js").read_text()
    )
    assert "installer_name: BUSINESS_LEGAL_NAME" in source
    assert "seller_name: f.seller_name || BUSINESS_LEGAL_NAME" in source


def test_step3_waits_for_template_metadata_before_generation(source: str):
    """A restored draft on Step 3 must not generate before template metadata
    is loaded, because required_fields live in /api/documents/templates."""
    assert "const [templatesLoading, setTemplatesLoading]" in source
    assert "setTemplatesLoading(true)" in source
    assert "Document templates are still loading" in source
    assert "const generateDisabled = templatesLoading || selected.length === 0" in source
    assert "disabled={generateDisabled}" in source
    assert "Selected document is no longer available" in source


def test_step3_surfaces_generation_blockers_instead_of_silently_staying_put(source: str):
    """Step 3 can be the target for packet/template quality blockers. Those
    errors must render in Step3 itself so admins know what to change."""
    step3_start = source.find("function Step3")
    step3_body = source[step3_start : source.find("/* ─── Step 4", step3_start)]
    assert "validationErrors" in step3_body
    assert "<ValidationErrors errors={validationErrors} />" in step3_body
    assert "Choose a recommended packet or select at least one individual document" in step3_body
    assert "No document templates are available right now." in step3_body
    assert "No packet presets are available." in step3_body
    assert "validationErrors={validationErrors}" in source


def test_generation_errors_preserve_backend_missing_field_guidance(source: str):
    """If the backend returns a structured missing-fields envelope, the
    frontend should route the admin back to the relevant step with the same
    inline guidance instead of a generic generation failure."""
    assert "function normalizeBackendMissingFields" in source
    assert "function describeGenerationFailure" in source
    assert "payload?.missing_fields || payload?.missing || payload?.fields" in source
    assert "setValidationErrors(failure.missingMessages)" in source
    assert "setMissingFields(failure.missingFields)" in source
    assert "setStep(failure.step)" in source
    assert "Document service returned" in source
    assert "d.success === false" in source


def test_generation_errors_surface_all_failed_batch_results(source: str):
    """A 200 response with success=false must still render as a generation
    failure instead of a green '0 documents ready' screen."""
    assert "function describeDocumentFailures" in source
    assert "payload?.documents" in source
    assert "payload?.documents_skipped" in source
    assert "d.success === false" in source
    assert "'Selected document'" in source
    assert "'Generation failed'" in source


def test_generation_auth_expiry_routes_staff_back_to_retryable_step(source: str):
    """If the admin session expires while generating, staff should see a clear
    retry instruction on document selection instead of a generic packet failure."""
    assert "ADMIN_SESSION_EXPIRED_GENERATION_MESSAGE" in source
    assert "Your customer/deal data and selected documents are still saved" in source
    generate_start = source.find("const generate = async () =>")
    generate_body = source[generate_start : source.find("const goToStep", generate_start)]
    assert "isAdminAuthExpiredResponse(r)" in generate_body
    assert "setGenErr(ADMIN_SESSION_EXPIRED_GENERATION_MESSAGE)" in generate_body
    assert "setStep(3)" in generate_body


def test_template_load_auth_expiry_uses_session_guidance(source: str):
    """Template/readiness 401s should tell staff to re-authenticate, not imply
    that the document template system itself disappeared."""
    assert "ADMIN_SESSION_EXPIRED_LOAD_MESSAGE" in source
    assert "refresh the Document Center before generating packets" in source
    assert "isAdminAuthExpiredResponse(readinessResponse)" in source
    assert "isAdminAuthExpiredResponse(r)" in source


def test_customer_search_load_remounts_visible_form_fields(source: str):
    """Loading an existing customer must update both form state and the
    uncontrolled visible inputs, or staff can unknowingly generate from stale
    half-filled customer data."""
    assert "onLoadCustomer" in source
    assert "const loadCustomerRecord = useCallback" in source
    assert "setFormResetKey(k => k + 1)" in source
    load_customer_start = source.find("const loadCustomer = (cust)")
    load_customer_body = source[
        load_customer_start : source.find("const filtered", load_customer_start)
    ]
    assert "const patch = {" in load_customer_body
    assert "onLoadCustomer(patch)" in load_customer_body


def test_step3_blocks_generation_until_deal_data_is_complete(source: str):
    """Selected packets should not let staff generate official-looking PDFs
    from a 30%-filled customer/deal record."""
    assert "function getDocumentCompletenessState" in source
    assert "DOCUMENT_PACKET_BASELINE_FIELDS" in source
    assert "Deal data needs attention" in source
    assert "Complete ${readinessErrors.length} required data item" in source
    assert "documentReadiness={getDocumentCompletenessState(form, templates, selDocs)}" in source
    assert "readinessErrors.length > 0" in source
    assert "setMissingFields(documentCompleteness.missing)" in source


def test_step3_makes_not_ready_packets_unselectable(source: str):
    """If the API marks any packet as not ready, staff should see that status
    before selecting it, not only after a failed generation attempt."""
    assert "function getPacketBlockedTemplates" in source
    assert "packetNotReady" in source
    assert "NOT READY" in source
    assert "Use the Standard Closing Packet or ready individual documents." in source
    assert "disabled={isPacketDisabled}" in source
    assert (
        "const isExactPacketSelection = selected.length === cnt && selectedCount === cnt" in source
    )
    assert "return isExactPacketSelection ? [] : [...tpls]" in source


def test_step3_makes_not_ready_individual_documents_unselectable(source: str):
    assert (
        "const productionBlockMessage = getTemplateProductionBlockMessage(doc.template_name)"
        in source
    )
    assert "const docNotReady = Boolean(productionBlockMessage)" in source
    assert "disabled={docNotReady}" in source
    assert "Not ready for Document Center yet: lender/note mapping is incomplete." in source


def test_step3_completeness_requires_installation_contact_and_home_fields(source: str):
    """The UI should call out the concrete fields Mark described as the
    difference between valid info and garbage-in document output."""
    for text in (
        "Buyer phone",
        "Installation street address",
        "Installation county",
        "Serial # 1",
        "Sales price is required and must be greater than $0",
    ):
        assert text in source


def test_step3_selection_changes_clear_stale_errors(source: str):
    """Changing the Step 3 selection should clear old generate/validation
    errors so the user gets fresh feedback for the new packet choice."""
    toggle_start = source.find("const toggleDoc = useCallback")
    toggle_body = source[toggle_start : source.find("}, []);", toggle_start)]
    assert "setGenErr('')" in toggle_body
    assert "setValidationErrors([])" in toggle_body
    select_start = source.find("const selectPacket = useCallback")
    select_body = source[select_start : source.find("}, []);", select_start)]
    assert "setGenErr('')" in select_body
    assert "setValidationErrors([])" in select_body


def test_selected_template_missing_fields_highlight_step2_inputs(source: str):
    """Template-required installation and pricing fields should get the same
    inline highlight treatment as manufacturer/model/serial."""
    for field in (
        "error={errOf('buyer_address')}",
        "error={errOf('buyer_city')}",
        "error={errOf('buyer_county')}",
        "error={errOf('buyer_state')}",
        "error={errOf('buyer_zip')}",
        "error={errOf('manufacturer_address')}",
        "error={errOf('manufacturer_city')}",
        "error={errOf('manufacturer_state')}",
        "error={errOf('manufacturer_zip')}",
        "error={errOf('sales_price')}",
    ):
        assert field in source


def test_manufacturer_location_required_fields_have_human_guidance(source: str):
    """TDHCA manufacturer-location blanks should route users to the concrete
    Step 2 inputs rather than silently leaving official PDF fields empty."""
    assert "manufacturer_address: 'Manufacturer address'" in source
    assert "manufacturer_city_state_zip: 'Manufacturer city/state/ZIP'" in source
    assert (
        "manufacturer_city_state_zip: ['manufacturer_city', 'manufacturer_state', 'manufacturer_zip']"
        in source
    )
    assert "Manufacturer ZIP" in source


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
