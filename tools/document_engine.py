"""
Document Engine — Data-driven PDF generation using field_map.json.
Replaces hardcoded per-template functions with a single generic pipeline.
Reuses fill_pdf_form() from document_tools.py for actual PDF writing.
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from pypdf import PdfReader, PdfWriter

from config.field_map_loader import (
    get_template_config,
    get_template_field_map,
    get_template_checkbox_fields,
    get_template_static_values,
    get_template_required_fields,
    get_field_definitions,
    get_packet_config,
    list_templates as _list_templates,
    list_packets as _list_packets,
    get_fields_for_template,
)
from tools.document_tools import fill_pdf_form, DOCUMENTS_DIR, OUTPUT_DIR

logger = logging.getLogger(__name__)


def generate_document(
    template_name: str,
    data: Dict[str, Any],
    output_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a filled PDF document from any mapped template.

    Args:
        template_name: The PDF filename (e.g., "TMHA_SalesContract.pdf")
        data: Dictionary of business data fields (e.g., {"buyer_name": "John Doe", ...})
        output_filename: Optional custom output filename. Auto-generated if not provided.

    Returns:
        Dict with keys: success, file_path, filename, message
    """
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
        import re
        safe_name = template_name.replace(".pdf", "")
        buyer = (
            data.get("buyer_name")
            or data.get("buyer_city")
            or data.get("manufacturer")
            or "Doc"
        )
        # Strip everything except alphanumeric, spaces, hyphens
        buyer = re.sub(r"[^a-zA-Z0-9\s\-]", "", buyer).replace(" ", "_")[:50]
        date_str = datetime.now().strftime("%Y%m%d")
        output_filename = f"{safe_name}_{buyer}_{date_str}.pdf"

    # Log without PII
    logger.info(f"Generating document: {template_name} -> {output_filename}")

    try:
        output_path = fill_pdf_form(template_path, pdf_data, output_filename)
        return {
            "success": True,
            "file_path": output_path,
            "filename": output_filename,
            "message": f"{config.get('display_name', template_name)} generated successfully",
        }
    except Exception as e:
        logger.error(f"Document generation failed for {template_name}: {e}")
        return {"success": False, "message": str(e)}


def generate_packet(
    packet_name: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a closing packet: multiple templates merged into a single PDF.

    Args:
        packet_name: The packet name from field_map.json (e.g., "standard_closing")
        data: Dictionary of business data fields shared across all templates.

    Returns:
        Dict with keys: success, file_path, filename, message, page_count, documents_included
    """
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

    if packet_config.get("include_cover_page"):
        cover_template = packet_config.get("cover_page_template", "All_Cover.pdf")
        cover_config = get_template_config(cover_template)
        if cover_config:
            cover_result = generate_document(cover_template, data, f"_cover_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf")
            if cover_result["success"]:
                generated_files.append(cover_result["file_path"])
                documents_included.append(cover_template)

    # Generate each template
    for template_name in template_names:
        result = generate_document(
            template_name, data,
            f"_packet_{template_name.replace('.pdf', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        )
        if result["success"]:
            generated_files.append(result["file_path"])
            documents_included.append(template_name)
        else:
            logger.warning(f"Skipping {template_name} in packet: {result['message']}")

    if not generated_files:
        return {"success": False, "message": "No documents were generated successfully"}

    # Merge all PDFs
    buyer = data.get("buyer_name", "Unknown").replace(" ", "_")
    date_str = datetime.now().strftime("%Y%m%d")
    packet_filename = f"Packet_{packet_name}_{buyer}_{date_str}.pdf"
    packet_path = os.path.join(OUTPUT_DIR, packet_filename)

    try:
        writer = PdfWriter()
        for file_path in generated_files:
            reader = PdfReader(file_path)
            for page in reader.pages:
                writer.add_page(page)

        with open(packet_path, "wb") as f:
            writer.write(f)

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
            "message": f"{packet_config.get('display_name', packet_name)} generated with {len(documents_included)} documents",
            "page_count": len(writer.pages),
            "documents_included": documents_included,
        }
    except Exception as e:
        logger.error(f"Packet merge failed for {packet_name}: {e}")
        return {"success": False, "message": str(e)}


def list_available_templates() -> List[Dict[str, Any]]:
    """List all available document templates with metadata."""
    return _list_templates()


def list_available_packets() -> List[Dict[str, Any]]:
    """List all available document packets with metadata."""
    return _list_packets()


def get_template_fields(template_name: str) -> Optional[Dict[str, Any]]:
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
        sections[section].append({
            "field_name": name,
            "label": defn.get("label", name),
            "type": defn.get("type", "string"),
            "required": name in required,
            "default": defn.get("default"),
            "computed": defn.get("computed", False),
            "pii": defn.get("pii", False),
        })

    return {
        "template_name": template_name,
        "display_name": config.get("display_name", template_name),
        "category": config.get("category", "Other"),
        "sections": sections,
    }


def generate_batch(
    template_names: List[str],
    data: Dict[str, Any],
    merge: bool = True,
) -> Dict[str, Any]:
    """
    Generate multiple documents from a shared data dict and optionally merge.

    Args:
        template_names: List of PDF template filenames to generate.
        data: Shared business data fields.
        merge: If True, merge all successful PDFs into one file.

    Returns:
        Dict with keys: success, documents (per-template results), merged (optional).
    """
    results = []
    successful_files = []

    for template_name in template_names:
        result = generate_document(
            template_name,
            dict(data),  # copy so required-field validation doesn't mutate across templates
            f"_batch_{template_name.replace('.pdf', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
        )
        config = get_template_config(template_name) or {}
        doc_result = {
            "template_name": template_name,
            "display_name": config.get("display_name", template_name),
            "success": result["success"],
            "filename": result.get("filename"),
            "download_url": f"/api/documents/download/{result['filename']}" if result.get("filename") else None,
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

            buyer = data.get("buyer_name", "Customer").replace(" ", "_")
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            merged_filename = f"Documents_{buyer}_{date_str}.pdf"
            merged_path = os.path.join(OUTPUT_DIR, merged_filename)
            with open(merged_path, "wb") as f:
                writer.write(f)

            merged = {
                "filename": merged_filename,
                "download_url": f"/api/documents/download/{merged_filename}",
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


def get_all_field_definitions() -> Dict[str, Any]:
    """Return all data field definitions from field_map.json for the unified form."""
    return get_field_definitions()


def _compute_fields(data: Dict[str, Any]):
    """Compute derived field values in-place."""
    # unpaid_balance = sales_price - down_payment
    sales_price = _to_float(data.get("sales_price"))
    down_payment = _to_float(data.get("down_payment"))
    if sales_price is not None:
        if down_payment is None:
            down_payment = 0.0
        data.setdefault("unpaid_balance", str(sales_price - down_payment))
        data.setdefault("total_unpaid_balance", str(sales_price - down_payment))

    # buyer_city_state_zip
    city = data.get("buyer_city", "")
    state = data.get("buyer_state", "TX")
    zip_code = data.get("buyer_zip", "")
    if city or zip_code:
        data.setdefault("buyer_city_state_zip", f"{city}, {state} {zip_code}".strip())


def _format_value(value: Any, field_def: Dict) -> str:
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


def _to_float(value: Any) -> Optional[float]:
    """Safely convert a value to float."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None
