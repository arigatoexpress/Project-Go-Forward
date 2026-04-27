"""
Deal -> document validation.

The Deal model's `to_document_data()` happily emits a dict with `None` values
silently stripped, so a half-filled deal flows straight into the document
engine and only fails deep inside PDF form-filling with a generic
"Missing required fields" error. The user then has no idea WHICH deal field
they forgot to fill in.

This module surfaces that information BEFORE document generation. It groups
required Deal fields by logical section (buyer identity, home identity, sale
pricing, financing, installation address) so the API can return a structured
report the frontend can render inline next to the offending form section.

Design notes:
  - We deliberately do NOT import or modify `database.models` here. PR #20
    owns models.py during this autonomous window, and re-validating field
    presence in a separate file lets both PRs land without merge conflicts.
  - We accept either a `dict` or a Pydantic `Deal` instance. Pydantic
    instances are converted via `model_dump()` so we always work against a
    plain dict internally.
  - "Missing" is defined narrowly: the key is absent, the value is None, or
    (for strings) the value is empty/whitespace-only. Numeric `0` counts as
    present — e.g. a $0 down payment is a real value, not a missing one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Required field groups for document generation.
#
# Each group corresponds to one logical section of the rendered document.
# The frontend can map group keys to UI sections (e.g. "buyer_identity"
# -> the buyer step in the deal wizard) so the user is taken straight to
# the offending form.
#
# Keep these field names in lockstep with `Deal` (database/models.py).
# If a new required field is added to Deal, append it here in the same
# group; if a field name is renamed in Deal, rename it here too.
_REQUIRED_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "buyer_identity": (
        "buyer_first_name",
        "buyer_last_name",
    ),
    "installation_address": (
        "buyer_address",
        "buyer_city",
        "buyer_zip",
    ),
    "home_identity": (
        "manufacturer",
        "model",
        "serial_number_1",
    ),
    "sale_pricing": (
        "sales_price",
        "down_payment",
    ),
    "financing": (
        "creditor_name",
        "loan_term",
        "apr",
    ),
}


def _coerce_to_dict(deal: Any) -> Mapping[str, Any]:
    """Accept a dict or a Pydantic Deal and return a plain dict view.

    Anything else raises TypeError so callers get a loud failure rather than
    a silently-empty validation report.
    """
    if isinstance(deal, Mapping):
        return deal
    # Pydantic v2 model
    model_dump = getattr(deal, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    # Pydantic v1 fallback (the THO repo is on v2, but this keeps the helper
    # safe to use from one-off scripts that might pre-date the upgrade).
    pydantic_dict = getattr(deal, "dict", None)
    if callable(pydantic_dict):
        return pydantic_dict()
    raise TypeError(
        f"validate_for_documents expected a dict or Pydantic Deal, got {type(deal).__name__}"
    )


def _is_missing(value: Any) -> bool:
    """Treat None and blank strings as missing; everything else is present.

    Numeric `0` is intentionally treated as present — a $0 down payment
    or a 0-month loan term is a real (if unusual) value the user typed,
    not a missing field.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _missing_in_group(data: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    """Return the subset of `fields` that are missing from `data`."""
    return [field for field in fields if _is_missing(data.get(field))]


def validate_for_documents(deal: Any) -> dict[str, list[str]]:
    """Report which Deal field groups are incomplete for document generation.

    Args:
        deal: A Deal-shaped dict OR a Pydantic Deal instance.

    Returns:
        A mapping of group name -> list of missing field names within that
        group. Groups that are fully populated are omitted entirely, so an
        empty dict means "this deal is ready for document generation".

        Example:
            {
                "buyer_identity": ["buyer_first_name", "buyer_last_name"],
                "financing": ["apr"],
            }

    Raises:
        TypeError: If `deal` is neither a Mapping nor a Pydantic model.
    """
    data = _coerce_to_dict(deal)

    report: dict[str, list[str]] = {}
    for group_name, fields in _REQUIRED_FIELD_GROUPS.items():
        missing = _missing_in_group(data, fields)
        if missing:
            report[group_name] = missing
    return report


__all__ = ["validate_for_documents"]
