"""
Document Engine — Data-driven PDF generation using field_map.json.
Replaces hardcoded per-template functions with a single generic pipeline.
Reuses fill_pdf_form() from document_tools.py for actual PDF writing.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

from pypdf import PdfReader, PdfWriter

from config.field_map_loader import (
    get_field_definitions,
    get_fields_for_template,
    get_packet_config,
    get_template_checkbox_fields,
    get_template_config,
    get_template_field_map,
    get_template_required_fields,
    get_template_static_values,
)
from config.field_map_loader import (
    list_packets as _list_packets,
)
from config.field_map_loader import (
    list_templates as _list_templates,
)
from tools.document_quality import (
    BUSINESS_ADDRESS,
    BUSINESS_CITY,
    BUSINESS_CITY_STATE_ZIP,
    BUSINESS_NAME,
    BUSINESS_PHONE,
    BUSINESS_RBI,
    BUSINESS_STATE,
    BUSINESS_ZIP,
)
from tools.document_tools import DOCUMENTS_DIR, OUTPUT_DIR, fill_pdf_form, upload_to_gcs
from tools.drive_service import ensure_deal_folder, upload_to_drive

logger = logging.getLogger(__name__)


def _has_value(value: Any) -> bool:
    """Return True for non-empty values; numeric zero is valid document data."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _set_if_missing(data: dict[str, Any], key: str, value: Any) -> None:
    if not _has_value(data.get(key)) and _has_value(value):
        data[key] = value


def _join_name(first: Any, last: Any) -> str | None:
    parts = [str(part).strip() for part in (first, last) if _has_value(part)]
    return " ".join(parts) if parts else None


def _city_state_zip(city: Any, state: Any, zip_code: Any) -> str | None:
    if not (_has_value(city) or _has_value(zip_code)):
        return None
    city_part = str(city).strip() if _has_value(city) else ""
    state_part = str(state).strip() if _has_value(state) else "TX"
    zip_part = str(zip_code).strip() if _has_value(zip_code) else ""
    prefix = f"{city_part}, " if city_part else ""
    return f"{prefix}{state_part} {zip_part}".strip()


def _full_address(address: Any, city_state_zip: Any) -> str | None:
    if not _has_value(address):
        return None
    address_part = str(address).strip()
    if _has_value(city_state_zip):
        return f"{address_part}, {str(city_state_zip).strip()}"
    return address_part


def _join_nonempty(parts: list[Any], separator: str = " | ") -> str | None:
    clean = [str(part).strip() for part in parts if _has_value(part)]
    return separator.join(clean) if clean else None


def _safe_filename_token(value: Any, default: str = "Doc", max_length: int = 50) -> str:
    raw = str(value or default)
    token = re.sub(r"[^a-zA-Z0-9_\s\-]", "", raw).strip().replace(" ", "_")
    token = token[:max_length].strip("_")
    return token or default


def _set_status_flags(
    data: dict[str, Any],
    status_key: str,
    married_key: str,
    unmarried_key: str,
    separated_key: str,
) -> None:
    status = str(data.get(status_key, "")).strip().lower()
    if not status:
        return
    if status == "married":
        _set_if_missing(data, married_key, True)
    elif status in {"single", "unmarried"}:
        _set_if_missing(data, unmarried_key, True)
    elif status == "separated":
        _set_if_missing(data, separated_key, True)


def _normalize_document_data(data: dict[str, Any]) -> dict[str, Any]:
    """Fill derived document aliases expected by field_map.json.

    Deal.to_document_data already computes these for deal-specific endpoints.
    The Document Center batch endpoint can receive raw form-shaped data, so the
    engine also normalizes at the generation boundary.
    """
    normalized = dict(data or {})

    _set_if_missing(
        normalized,
        "buyer_name",
        _join_name(normalized.get("buyer_first_name"), normalized.get("buyer_last_name")),
    )
    _set_if_missing(
        normalized,
        "co_buyer_name",
        _join_name(
            normalized.get("co_buyer_first_name"),
            normalized.get("co_buyer_last_name"),
        ),
    )

    buyer_city_state_zip = _city_state_zip(
        normalized.get("buyer_city"),
        normalized.get("buyer_state"),
        normalized.get("buyer_zip"),
    )
    _set_if_missing(normalized, "buyer_city_state_zip", buyer_city_state_zip)
    _set_if_missing(
        normalized,
        "buyer_full_address",
        _full_address(normalized.get("buyer_address"), normalized.get("buyer_city_state_zip")),
    )

    mailing_city_state_zip = _city_state_zip(
        normalized.get("mailing_city"),
        normalized.get("mailing_state"),
        normalized.get("mailing_zip"),
    )
    _set_if_missing(normalized, "mailing_city_state_zip", mailing_city_state_zip)
    _set_if_missing(
        normalized,
        "mailing_full_address",
        _full_address(
            normalized.get("mailing_address"),
            normalized.get("mailing_city_state_zip"),
        ),
    )

    _set_if_missing(
        normalized,
        "manufacturer_model",
        _join_nonempty([normalized.get("manufacturer"), normalized.get("model")], " "),
    )
    manufacturer_city_state_zip = _city_state_zip(
        normalized.get("manufacturer_city"),
        normalized.get("manufacturer_state"),
        normalized.get("manufacturer_zip"),
    )
    _set_if_missing(normalized, "manufacturer_city_state_zip", manufacturer_city_state_zip)
    _set_if_missing(
        normalized,
        "manufacturer_full_address",
        _full_address(
            normalized.get("manufacturer_address"),
            normalized.get("manufacturer_city_state_zip"),
        ),
    )
    _set_if_missing(
        normalized,
        "manufacturer_model_hud",
        _join_nonempty(
            [normalized.get("manufacturer_model"), normalized.get("label_number_1")],
            " / ",
        ),
    )
    _set_if_missing(
        normalized,
        "manufacturer_model_serial",
        _join_nonempty(
            [normalized.get("manufacturer_model"), normalized.get("serial_number_1")],
            " / ",
        ),
    )

    _set_if_missing(normalized, "seller_name", BUSINESS_NAME)
    _set_if_missing(normalized, "seller_rbi", BUSINESS_RBI)
    _set_if_missing(normalized, "seller_phone", BUSINESS_PHONE)
    _set_if_missing(normalized, "seller_address", BUSINESS_ADDRESS)
    _set_if_missing(normalized, "seller_city", BUSINESS_CITY)
    _set_if_missing(normalized, "seller_state", BUSINESS_STATE)
    _set_if_missing(normalized, "seller_zip", BUSINESS_ZIP)
    _set_if_missing(normalized, "seller_city_state_zip", BUSINESS_CITY_STATE_ZIP)

    if str(normalized.get("installer_type", "")).strip().lower() != "other":
        _set_if_missing(normalized, "installer_name", BUSINESS_NAME)
        _set_if_missing(normalized, "installer_phone", BUSINESS_PHONE)
        _set_if_missing(normalized, "installer_address", BUSINESS_ADDRESS)
        _set_if_missing(normalized, "installer_city", BUSINESS_CITY)
        _set_if_missing(normalized, "installer_state", BUSINESS_STATE)
        _set_if_missing(normalized, "installer_zip", BUSINESS_ZIP)

    installer_city_state_zip = _city_state_zip(
        normalized.get("installer_city"),
        normalized.get("installer_state"),
        normalized.get("installer_zip"),
    )
    _set_if_missing(normalized, "installer_city_state_zip", installer_city_state_zip)
    _set_if_missing(
        normalized,
        "installer_address_city_state_zip",
        _full_address(
            normalized.get("installer_address"),
            normalized.get("installer_city_state_zip"),
        ),
    )
    _set_if_missing(
        normalized,
        "installer_name_address",
        _join_nonempty(
            [
                normalized.get("installer_name"),
                normalized.get("installer_address_city_state_zip"),
            ],
            ", ",
        ),
    )
    _set_if_missing(normalized, "installer_contact_name", normalized.get("installer_name"))
    _set_if_missing(normalized, "installer_contact_phone", normalized.get("installer_phone"))

    _set_if_missing(
        normalized,
        "serial_label_combined",
        _join_nonempty(
            [
                f"S/N: {normalized['serial_number_1']}"
                if _has_value(normalized.get("serial_number_1"))
                else None,
                f"S/N2: {normalized['serial_number_2']}"
                if _has_value(normalized.get("serial_number_2"))
                else None,
                f"HUD: {normalized['label_number_1']}"
                if _has_value(normalized.get("label_number_1"))
                else None,
                f"HUD2: {normalized['label_number_2']}"
                if _has_value(normalized.get("label_number_2"))
                else None,
            ],
        ),
    )

    own_rent = str(normalized.get("mailing_own_rent", "")).strip().lower()
    if own_rent in {"own", "owns", "o"}:
        _set_if_missing(normalized, "buyer_owns_residence", True)
    elif own_rent in {"rent", "rents", "r"}:
        _set_if_missing(normalized, "buyer_rents_residence", True)

    _set_status_flags(
        normalized,
        "buyer_marital_status",
        "buyer_married",
        "buyer_unmarried",
        "buyer_separated",
    )
    _set_status_flags(
        normalized,
        "co_buyer_marital_status",
        "co_buyer_married",
        "co_buyer_unmarried",
        "co_buyer_separated",
    )

    return normalized


def generate_document(
    template_name: str,
    data: dict[str, Any],
    output_filename: str | None = None,
    deal_id: str | None = None,
) -> dict[str, Any]:
    """
    Generate a filled PDF document from any mapped template.

    Args:
        template_name: The PDF filename (e.g., "TMHA_SalesContract.pdf")
        data: Dictionary of business data fields (e.g., {"buyer_name": "John Doe", ...})
        output_filename: Optional custom output filename. Auto-generated if not provided.

    Returns:
        Dict with keys: success, file_path, filename, message
    """
    data = _normalize_document_data(data)

    # Load template config
    config = get_template_config(template_name)
    if config is None:
        return {
            "success": False,
            "message": f"Template '{template_name}' not found in field_map.json",
        }

    # Verify template PDF exists
    template_path = os.path.join(DOCUMENTS_DIR, template_name)
    if not os.path.exists(template_path):
        return {
            "success": False,
            "message": f"Template file not found: {template_path}",
        }

    # Apply default values from field definitions (before validation)
    field_defs = get_field_definitions()
    for field_name, defn in field_defs.items():
        if field_name not in data or data[field_name] is None or data[field_name] == "":
            default = defn.get("default")
            if default is not None:
                data[field_name] = default

    # Validate required fields (after defaults applied)
    required = get_template_required_fields(template_name)
    missing = [f for f in required if not data.get(f)]
    if missing:
        return {
            "success": False,
            "message": f"Missing required fields: {', '.join(missing)}",
        }

    # Compute derived fields
    _compute_fields(data)

    # Build the PDF field dict by inverting the mapping (data_field -> pdf_field)
    pdf_data = {}

    # Text fields
    field_map = get_template_field_map(template_name)
    for pdf_field, data_field in field_map.items():
        value = data.get(data_field, "")
        if value is None:
            value = ""
        # Format by type
        defn = field_defs.get(data_field, {})
        value = _format_value(value, defn)
        pdf_data[pdf_field] = value

    # Checkbox fields
    checkbox_map = get_template_checkbox_fields(template_name)
    for pdf_field, data_field in checkbox_map.items():
        value = data.get(data_field, False)
        if value in (True, "true", "True", "1", "yes", "Yes"):
            pdf_data[pdf_field] = "Yes"
        # Leave unchecked checkboxes unset

    # Static values (overwrite any data-driven values)
    static = get_template_static_values(template_name)
    pdf_data.update(static)

    # Generate output filename (sanitized to prevent path traversal)
    if not output_filename:
        safe_name = _safe_filename_token(
            template_name.replace(".pdf", ""),
            default="Document",
            max_length=80,
        )
        buyer = (
            data.get("buyer_name") or data.get("buyer_city") or data.get("manufacturer") or "Doc"
        )
        buyer = _safe_filename_token(buyer)
        date_str = datetime.now().strftime("%Y%m%d")
        output_filename = f"{safe_name}_{buyer}_{date_str}.pdf"

    # Log without PII
    logger.info(f"Generating document: {template_name} -> {output_filename}")

    try:
        output_path = fill_pdf_form(template_path, pdf_data, output_filename)

        # Mirror to Drive if deal_id is provided
        drive_url = None
        current_deal_id = deal_id or data.get("deal_id")
        if current_deal_id:
            deal_folder_id = ensure_deal_folder(current_deal_id)
            if deal_folder_id:
                drive_url = upload_to_drive(output_path, output_filename, deal_folder_id)

        return {
            "success": True,
            "file_path": output_path,
            "filename": output_filename,
            "drive_url": drive_url,
            "message": f"{config.get('display_name', template_name)} generated successfully",
        }
    except Exception as e:
        logger.error(f"Document generation failed for {template_name}: {e}")
        return {"success": False, "message": str(e)}


def generate_packet(
    packet_name: str,
    data: dict[str, Any],
    deal_id: str | None = None,
) -> dict[str, Any]:
    """
    Generate a closing packet: multiple templates merged into a single PDF.

    Args:
        packet_name: The packet name from field_map.json (e.g., "standard_closing")
        data: Dictionary of business data fields shared across all templates.

    Returns:
        Dict with keys: success, file_path, filename, message, page_count, documents_included
    """
    data = _normalize_document_data(data)

    packet_config = get_packet_config(packet_name)
    if packet_config is None:
        return {
            "success": False,
            "message": f"Packet '{packet_name}' not found in field_map.json",
        }

    template_names = packet_config.get("templates", [])
    if not template_names:
        return {"success": False, "message": "Packet has no templates defined"}

    # Generate cover page first if configured
    generated_files = []
    documents_included = []
    documents_skipped: list[dict[str, str]] = []

    if packet_config.get("include_cover_page"):
        cover_template = packet_config.get("cover_page_template", "All_Cover.pdf")
        cover_config = get_template_config(cover_template)
        if cover_config:
            try:
                cover_result = generate_document(
                    cover_template,
                    data,
                    f"_cover_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
                    deal_id=deal_id,
                )
            except TypeError:
                # Fallback for mocked generate_document without deal_id
                cover_result = generate_document(
                    cover_template,
                    data,
                    f"_cover_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
                )
            if cover_result["success"]:
                generated_files.append(cover_result["file_path"])
                documents_included.append(cover_template)
            else:
                documents_skipped.append(
                    {"template": cover_template, "reason": cover_result.get("message", "")}
                )
                logger.warning(
                    f"Skipping cover page {cover_template} in packet: {cover_result['message']}"
                )

    # Generate each template
    for template_name in template_names:
        try:
            result = generate_document(
                template_name,
                data,
                f"_packet_{template_name.replace('.pdf', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
                deal_id=deal_id,
            )
        except TypeError:
            # Fallback for mocked generate_document without deal_id
            result = generate_document(
                template_name,
                data,
                f"_packet_{template_name.replace('.pdf', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
            )
        if result["success"]:
            generated_files.append(result["file_path"])
            documents_included.append(template_name)
        else:
            documents_skipped.append(
                {"template": template_name, "reason": result.get("message", "")}
            )
            logger.warning(f"Skipping {template_name} in packet: {result['message']}")

    if not generated_files:
        return {
            "success": False,
            "message": "No documents were generated successfully",
            "documents_skipped": documents_skipped,
        }

    # Merge all PDFs
    buyer = data.get("buyer_name", "Unknown").replace(" ", "_")
    date_str = datetime.now().strftime("%Y%m%d")
    packet_filename = f"Packet_{packet_name}_{buyer}_{date_str}.pdf"
    packet_path = os.path.join(OUTPUT_DIR, packet_filename)

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        writer = PdfWriter()
        for file_path in generated_files:
            reader = PdfReader(file_path)
            for page in reader.pages:
                writer.add_page(page)

        with open(packet_path, "wb") as f:
            writer.write(f)
        upload_to_gcs(packet_path, packet_filename)

        # Mirror packet to Drive
        drive_url = None
        current_deal_id = deal_id or data.get("deal_id")
        if current_deal_id:
            deal_folder_id = ensure_deal_folder(current_deal_id)
            if deal_folder_id:
                drive_url = upload_to_drive(packet_path, packet_filename, deal_folder_id)

        # Clean up individual files
        for file_path in generated_files:
            try:
                os.remove(file_path)
            except OSError:
                pass

        return {
            "success": True,
            "file_path": packet_path,
            "filename": packet_filename,
            "drive_url": drive_url,
            "message": f"{packet_config.get('display_name', packet_name)} generated with {len(documents_included)} documents",
            "page_count": len(writer.pages),
            "documents_included": documents_included,
            "documents_skipped": documents_skipped,
        }
    except Exception as e:
        logger.error(f"Packet merge failed for {packet_name}: {e}")
        return {"success": False, "message": str(e), "documents_skipped": documents_skipped}


def list_available_templates() -> list[dict[str, Any]]:
    """List all available document templates with metadata."""
    return _list_templates()


def list_available_packets() -> list[dict[str, Any]]:
    """List all available document packets with metadata."""
    return _list_packets()


def get_template_fields(template_name: str) -> dict[str, Any] | None:
    """
    Get field definitions for a specific template (drives frontend SmartForm).
    Returns None if template not found.
    """
    config = get_template_config(template_name)
    if config is None:
        return None

    fields = get_fields_for_template(template_name)
    required = set(get_template_required_fields(template_name))

    # Group by section
    sections = {}
    for name, defn in fields.items():
        section = defn.get("section", "other")
        if section not in sections:
            sections[section] = []
        sections[section].append(
            {
                "field_name": name,
                "label": defn.get("label", name),
                "type": defn.get("type", "string"),
                "required": name in required,
                "default": defn.get("default"),
                "computed": defn.get("computed", False),
                "pii": defn.get("pii", False),
            }
        )

    return {
        "template_name": template_name,
        "display_name": config.get("display_name", template_name),
        "category": config.get("category", "Other"),
        "sections": sections,
    }


def generate_batch(
    template_names: list[str],
    data: dict[str, Any],
    merge: bool = True,
    deal_id: str | None = None,
) -> dict[str, Any]:
    """
    Generate multiple documents from a shared data dict and optionally merge.

    Args:
        template_names: List of PDF template filenames to generate.
        data: Shared business data fields.
        merge: If True, merge all successful PDFs into one file.

    Returns:
        Dict with keys: success, documents (per-template results), merged (optional).
    """
    data = _normalize_document_data(data)

    results = []
    successful_files = []
    batch_buyer = _safe_filename_token(
        data.get("buyer_name") or data.get("buyer_city") or data.get("manufacturer") or "Customer",
        default="Customer",
    )

    for template_name in template_names:
        safe_template = _safe_filename_token(
            template_name.replace(".pdf", ""),
            default="Document",
            max_length=80,
        )
        try:
            result = generate_document(
                template_name,
                dict(data),  # copy so required-field validation doesn't mutate across templates
                f"_batch_{safe_template}_{batch_buyer}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
                deal_id=deal_id,
            )
        except TypeError:
            # Fallback for mocked generate_document that doesn't accept deal_id
            result = generate_document(
                template_name,
                dict(data),
                f"_batch_{safe_template}_{batch_buyer}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
            )
        config = get_template_config(template_name) or {}
        doc_result = {
            "template_name": template_name,
            "display_name": config.get("display_name", template_name),
            "success": result["success"],
            "filename": result.get("filename"),
            "download_url": f"/api/documents/download/{result['filename']}"
            if result.get("filename")
            else None,
            "message": result.get("message", ""),
        }
        results.append(doc_result)
        if result["success"] and result.get("file_path"):
            successful_files.append(result["file_path"])

    merged = None
    if merge and len(successful_files) > 1:
        try:
            writer = PdfWriter()
            for fpath in successful_files:
                reader = PdfReader(fpath)
                for page in reader.pages:
                    writer.add_page(page)

            buyer = _safe_filename_token(data.get("buyer_name", "Customer"), default="Customer")
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            merged_filename = f"Documents_{buyer}_{date_str}.pdf"
            merged_path = os.path.join(OUTPUT_DIR, merged_filename)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(merged_path, "wb") as f:
                writer.write(f)
            upload_to_gcs(merged_path, merged_filename)

            # Mirror merged batch to Drive
            drive_url = None
            current_deal_id = deal_id or data.get("deal_id")
            if current_deal_id:
                deal_folder_id = ensure_deal_folder(current_deal_id)
                if deal_folder_id:
                    drive_url = upload_to_drive(merged_path, merged_filename, deal_folder_id)

            merged = {
                "filename": merged_filename,
                "download_url": f"/api/documents/download/{merged_filename}",
                "drive_url": drive_url,
                "page_count": len(writer.pages),
                "document_count": len(successful_files),
            }
        except Exception as e:
            logger.error(f"Batch merge failed: {e}")

    return {
        "success": len(successful_files) > 0,
        "documents": results,
        "merged": merged,
        "total": len(template_names),
        "successful": len(successful_files),
    }


def get_all_field_definitions() -> dict[str, Any]:
    """Return all data field definitions from field_map.json for the unified form."""
    return get_field_definitions()


def _compute_fields(data: dict[str, Any]):
    """Compute derived field values in-place."""
    # unpaid_balance = sales_price - down_payment
    sales_price = _to_float(data.get("sales_price"))
    down_payment = _to_float(data.get("down_payment"))
    if sales_price is not None:
        if down_payment is None:
            down_payment = 0.0
        data.setdefault("unpaid_balance", str(sales_price - down_payment))
        data.setdefault("total_unpaid_balance", str(sales_price - down_payment))

    # ─── Escrow derivations (Mark Willcott spec, 2026-06-24) ───
    # Insurance: staff enters the ANNUAL premium; monthly INS escrow = annual / 12.
    annual_insurance = _to_float(data.get("annual_insurance"))
    if annual_insurance is not None:
        data.setdefault("insurance_escrow_monthly", f"{annual_insurance / 12:.2f}")

    # Tax: staff enters a TAX RATE (percent) and a taxable value
    # (default = sales_price when not supplied). annual_tax = (rate/100) * value;
    # monthly tax escrow = annual_tax / 12.
    tax_rate = _to_float(data.get("tax_rate"))
    if tax_rate is not None:
        taxable_value = _to_float(data.get("taxable_value"))
        if taxable_value is None:
            taxable_value = sales_price  # may be None if neither was provided
        if taxable_value is not None:
            annual_tax = (tax_rate / 100.0) * taxable_value
            data.setdefault("tax_escrow_monthly", f"{annual_tax / 12:.2f}")
    # TODO(escrow-pdf-mapping): Once Mark provides the actual escrow form
    # ("Adobe Scan Jun 24, 2026.pdf"), map insurance_escrow_monthly /
    # tax_escrow_monthly to the correct PDF field names in
    # config/field_map.json. Do NOT guess field names against an unseen form.

    # buyer_city_state_zip
    city = data.get("buyer_city", "")
    state = data.get("buyer_state", "TX")
    zip_code = data.get("buyer_zip", "")
    if city or zip_code:
        data.setdefault("buyer_city_state_zip", f"{city}, {state} {zip_code}".strip())


def _format_value(value: Any, field_def: dict) -> str:
    """Format a value based on its field definition type."""
    if value is None or value == "":
        return ""

    field_type = field_def.get("type", "string")

    if field_type == "currency":
        try:
            num = float(str(value).replace(",", "").replace("$", ""))
            return f"{num:,.2f}"
        except (ValueError, TypeError):
            return str(value)

    if field_type == "date":
        # Pass through — dates can be in various formats
        return str(value)

    return str(value)


def _to_float(value: Any) -> float | None:
    """Safely convert a value to float."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None
