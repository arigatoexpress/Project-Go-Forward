"""Packet de-duplication shared by every packet/batch path.

Rules live in config/field_map.json -> form_set_rules.duplicate_groups. Each
group lists the editions of one form newest first; only the first present
member is kept, so a packet never prints the same form twice.
"""

from __future__ import annotations

from config.field_map_loader import get_form_set_rules


def dedupe_packet_templates(template_names: list[str]) -> list[str]:
    """Drop older editions and exact repeats of the same form, preserving order."""
    result = list(dict.fromkeys(template_names))
    rules = get_form_set_rules() or {}
    for group in rules.get("duplicate_groups", []) or []:
        members = group.get("keep_first_present", []) or []
        present = [tpl for tpl in members if tpl in result]
        if len(present) <= 1:
            continue
        drop = set(present[1:])
        result = [tpl for tpl in result if tpl not in drop]
    return result
