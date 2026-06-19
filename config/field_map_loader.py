"""
Field Map Loader — Reads config/field_map.json and provides template mapping data to all modules.
Follows the same caching pattern as config_loader.py.
"""

import json
import os
from typing import Any

_field_map = None
_FIELD_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "field_map.json")


def get_field_map() -> dict:
    """Load and cache field_map.json."""
    global _field_map
    if _field_map is None:
        with open(_FIELD_MAP_PATH) as f:
            _field_map = json.load(f)
    return _field_map


def get_template_config(template_name: str) -> dict | None:
    """Get the full config for a specific template."""
    return get_field_map().get("templates", {}).get(template_name)


def get_template_field_map(template_name: str) -> dict[str, str] | None:
    """Get the PDF-field-to-data-field mapping for a template."""
    config = get_template_config(template_name)
    if config is None:
        return None
    return config.get("field_map", {})


def get_template_checkbox_fields(template_name: str) -> dict[str, str]:
    """Get the checkbox field mapping for a template."""
    config = get_template_config(template_name)
    if config is None:
        return {}
    return config.get("checkbox_fields", {})


def get_template_static_values(template_name: str) -> dict[str, str]:
    """Get static (constant) values for a template."""
    config = get_template_config(template_name)
    if config is None:
        return {}
    return config.get("static_values", {})


def get_template_required_fields(template_name: str) -> list[str]:
    """Get the list of required data fields for a template."""
    config = get_template_config(template_name)
    if config is None:
        return []
    return config.get("required_fields", [])


def get_template_pii_fields(template_name: str) -> list[str]:
    """Get the list of PII-sensitive fields for a template."""
    config = get_template_config(template_name)
    if config is None:
        return []
    return config.get("pii_fields", [])


def get_field_definitions() -> dict[str, dict]:
    """Get the master data field definitions schema."""
    return get_field_map().get("data_field_definitions", {})


def get_field_definition(field_name: str) -> dict | None:
    """Get the definition for a specific data field."""
    return get_field_definitions().get(field_name)


def get_packet_config(packet_name: str) -> dict | None:
    """Get the config for a specific document packet."""
    return get_field_map().get("packets", {}).get(packet_name)


def get_form_set_rules() -> dict[str, Any]:
    """Get Phase 3 form-set rules (dedupe / used-home suppression / flag gating)."""
    return get_field_map().get("form_set_rules", {})


def list_templates() -> list[dict[str, Any]]:
    """List all mapped templates with metadata (for API responses)."""
    templates = get_field_map().get("templates", {})
    result = []
    for name, config in templates.items():
        result.append(
            {
                "template_name": name,
                "display_name": config.get("display_name", name),
                "category": config.get("category", "Other"),
                "description": config.get("description", ""),
                "required_fields": config.get("required_fields", []),
                "field_count": len(config.get("field_map", {})),
            }
        )
    return result


def list_packets() -> list[dict[str, Any]]:
    """List all defined document packets with metadata."""
    packets = get_field_map().get("packets", {})
    result = []
    for name, config in packets.items():
        result.append(
            {
                "packet_name": name,
                "display_name": config.get("display_name", name),
                "description": config.get("description", ""),
                "template_count": len(config.get("templates", [])),
                "templates": config.get("templates", []),
            }
        )
    return result


def get_templates_by_category(category: str) -> list[dict[str, Any]]:
    """Get all templates in a specific category."""
    return [t for t in list_templates() if t["category"] == category]


def get_fields_for_template(template_name: str) -> dict[str, dict]:
    """
    Get the data field definitions relevant to a specific template.
    Returns only the fields that are used by this template's field_map.
    """
    config = get_template_config(template_name)
    if config is None:
        return {}

    # Collect all data field names used by this template
    used_fields = set()
    for pdf_field, data_field in config.get("field_map", {}).items():
        used_fields.add(data_field)
    for pdf_field, data_field in config.get("checkbox_fields", {}).items():
        used_fields.add(data_field)

    # Return matching definitions
    all_defs = get_field_definitions()
    return {name: defn for name, defn in all_defs.items() if name in used_fields}


def reload():
    """Force reload of field_map.json (useful after edits during development)."""
    global _field_map
    _field_map = None
    get_field_map()
