"""
PII Guard — Protects personally identifiable information from leaking to logs or LLM prompts.
"""

import re
from typing import Dict, Any, Set

from config.field_map_loader import get_field_definitions


# Patterns that indicate PII in free text
SSN_PATTERN = re.compile(r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b')
CREDIT_CARD_PATTERN = re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b')
BANK_ACCOUNT_PATTERN = re.compile(r'\b\d{8,17}\b')


def get_pii_field_names() -> Set[str]:
    """Get all field names marked as PII in data_field_definitions."""
    defs = get_field_definitions()
    return {name for name, defn in defs.items() if defn.get("pii", False)}


def sanitize_for_logging(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a copy of data with PII fields masked for safe logging.
    SSN becomes '***-**-1234', other PII becomes '***REDACTED***'.
    """
    pii_fields = get_pii_field_names()
    sanitized = {}

    for key, value in data.items():
        if key in pii_fields:
            if value and isinstance(value, str) and len(value) >= 4:
                sanitized[key] = f"***{value[-4:]}"
            else:
                sanitized[key] = "***REDACTED***"
        else:
            sanitized[key] = value

    return sanitized


def sanitize_for_llm(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a copy of data with PII fields completely removed.
    Used before sending data to Gemini or any LLM.
    """
    pii_fields = get_pii_field_names()
    return {k: v for k, v in data.items() if k not in pii_fields}


def validate_no_pii_in_text(text: str) -> Dict[str, Any]:
    """
    Scan text for PII patterns (SSN, credit card numbers).
    Returns dict with 'clean' (bool) and 'findings' (list of pattern matches).
    """
    findings = []

    ssn_matches = SSN_PATTERN.findall(text)
    if ssn_matches:
        findings.append({"type": "SSN", "count": len(ssn_matches)})

    cc_matches = CREDIT_CARD_PATTERN.findall(text)
    if cc_matches:
        findings.append({"type": "credit_card", "count": len(cc_matches)})

    return {
        "clean": len(findings) == 0,
        "findings": findings,
    }


def redact_pii_from_text(text: str) -> str:
    """
    Redact PII patterns from free text. Used for logging conversation excerpts.
    """
    text = SSN_PATTERN.sub('[SSN-REDACTED]', text)
    text = CREDIT_CARD_PATTERN.sub('[CC-REDACTED]', text)
    return text
