"""Deterministic-first triage classifier for inbound email.

Routes every inbound email into exactly one of two buckets:

- ``safe_ack``     — eligible for the fixed acknowledgement template (the only
                     unsupervised reply the system can ever send, and only when
                     ``FF_EMAIL_AUTO_ACK`` is enabled — default OFF).
- ``substantive``  — everything else: pricing, warranty, escrow, orders,
                     complaints, attachments, ambiguity, non-English, prompt
                     injection, or ANY doubt. Substantive mail gets a
                     human-approved draft, never an automatic reply.

Design rules (the golden set in ``tests/test_email_triage.py`` is the spec):

1. **Deterministic.** Pure function of (subject, body, sender). No LLM, no
   network, no clock. An LLM layer may later *demote* safe→substantive as an
   advisory narrowing — it must never promote substantive→safe.
2. **Fail-to-substantive.** Unknown, empty, HTML-only, over-long, non-ASCII
   heavy, parse failures, and internal errors ALL classify as substantive.
3. **Untrusted input.** Email content is data, never instructions. Injection
   markers hard-trigger substantive so injected text can never reach even the
   (body-echo-free) ack path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LABEL_SAFE_ACK = "safe_ack"
LABEL_SUBSTANTIVE = "substantive"

# Body length beyond which we refuse to call anything "simple".
_MAX_SAFE_BODY_CHARS = 600
# More than one question mark = a real conversation, not a simple ack.
_MAX_SAFE_QUESTION_MARKS = 1
# Minimum fraction of ASCII characters for the "plain English" heuristic.
_MIN_ASCII_RATIO = 0.9

# ── Hard substantive triggers ───────────────────────────────────────────────
# Any hit anywhere in subject or body → substantive. Kept intentionally broad:
# a false "substantive" costs a human a glance; a false "safe" is an outbound
# email nobody reviewed.
_SUBSTANTIVE_TRIGGERS: dict[str, re.Pattern] = {
    "money": re.compile(
        r"[$€£]|\b\d{1,3}(,\d{3})+\b|"
        r"\b(price|pricing|cost|costs|quote|quotes|afford|budget|payment|payments|"
        r"deposit|down\s*payment|finance|financing|financed|loan|loans|rate|rates|"
        r"credit|invoice|billing|billed|charge|charged|money|pay|paid|refund|refunds)\b",
        re.IGNORECASE,
    ),
    "legal": re.compile(
        r"\b(escrow|warranty|warranties|contract|contracts|attorney|lawyer|legal|"
        r"lawsuit|sue|suing|liability|lien|title|deed|notary|closing|clause|"
        r"agreement|dispute|arbitration)\b",
        re.IGNORECASE,
    ),
    "complaint": re.compile(
        r"\b(complaint|complain|unhappy|dissatisfied|angry|upset|terrible|awful|"
        r"unacceptable|disappointed|frustrat\w*|never\s+arrived|broken|damaged|"
        r"defect\w*|problem|problems|issue|issues|wrong|failed|failure)\b",
        re.IGNORECASE,
    ),
    "urgency": re.compile(r"\b(urgent|urgently|asap|immediately|emergency|right\s+away)\b", re.IGNORECASE),
    "order": re.compile(
        r"\b(order|orders|delivery|deliver|delivered|install|installation|installed|"
        r"status|tracking|schedule|scheduled|appointment|cancel|cancellation|"
        r"purchase|purchased|bought|my\s+home|our\s+home)\b",
        re.IGNORECASE,
    ),
    "attachment": re.compile(r"\b(attach|attached|attachment|attachments|enclosed|see\s+the\s+file)\b", re.IGNORECASE),
    "pii_ish": re.compile(r"\b(ssn|social\s+security|passport|driver'?s?\s+license|bank\s+account)\b", re.IGNORECASE),
}

# ── Prompt-injection markers ────────────────────────────────────────────────
# Inbound email is untrusted; these can NEVER classify as safe.
_INJECTION_MARKERS: re.Pattern = re.compile(
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?|"
    r"disregard\s+(all\s+)?(your|the|previous|prior)\s+(rules?|instructions?)|"
    r"system\s*prompt|developer\s+mode|jailbreak|"
    r"you\s+are\s+(now\s+)?(an?\s+ai|authorized|in)\b|"
    r"\bsystem\s*:|\bassistant\s*:|\buser\s*:|"
    r"send\s+(an?\s+)?email\s+to|forward\s+(all|any|future)\b|"
    r"\bbcc\b|auto[-\s]?reply\s+with|mark\s+this\s+sender\s+as|"
    r"reply\s+yes\s+to\s+confirm|admin\s+pin|api\s*key|password",
    re.IGNORECASE,
)

# ── Safe-ack positive patterns ──────────────────────────────────────────────
# SAFE requires at least one positive match — absence of triggers alone is
# not enough (unknown intent → substantive).
_SAFE_PATTERNS: dict[str, re.Pattern] = {
    "general_interest": re.compile(
        r"\b(interested|interest\s+in|more\s+information|more\s+info|info(rmation)?\s+about|"
        r"looking\s+(for|at)|saw\s+your\s+(listing|listings|website|ad))\b",
        re.IGNORECASE,
    ),
    "contact_request": re.compile(
        r"\b(call\s+me|contact\s+me|reach\s+me|get\s+back\s+to\s+me|call\s+back|give\s+me\s+a\s+call)\b",
        re.IGNORECASE,
    ),
    "hours_location": re.compile(
        r"\b(hours|open|closed|location|address|directions|where\s+are\s+you)\b", re.IGNORECASE
    ),
    "greeting_thanks": re.compile(
        r"\b(thank\s+you|thanks|looking\s+forward|stop\s+by|visit(ing)?|come\s+by)\b", re.IGNORECASE
    ),
}

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class TriageResult:
    """Outcome of classifying one inbound email."""

    label: str
    reason: str
    rule_hits: list[str] = field(default_factory=list)


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", text)


def _ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if ord(ch) < 128) / len(text)


def _evaluate_rules(subject: str, body: str) -> TriageResult:
    """Core rule table. Called only with sanitized string inputs."""
    text = f"{subject}\n{body}".strip()
    plain = _strip_html(text).strip()
    plain_body = _strip_html(body).strip()

    # Degenerate input: nothing to classify → substantive (human looks).
    if not plain or not plain_body:
        return TriageResult(LABEL_SUBSTANTIVE, "empty or HTML-only content", ["empty_content"])

    hits: list[str] = []

    # 1. Injection markers — absolute veto.
    if _INJECTION_MARKERS.search(plain):
        return TriageResult(
            LABEL_SUBSTANTIVE, "prompt-injection marker present", ["injection_marker"]
        )

    # 2. Hard substantive triggers.
    for name, pattern in _SUBSTANTIVE_TRIGGERS.items():
        if pattern.search(plain):
            hits.append(f"trigger:{name}")
    if hits:
        return TriageResult(LABEL_SUBSTANTIVE, "substantive trigger matched", hits)

    # 3. Shape heuristics — anything non-simple is substantive.
    if len(plain_body) > _MAX_SAFE_BODY_CHARS:
        return TriageResult(LABEL_SUBSTANTIVE, "body too long for safe subset", ["too_long"])
    if plain.count("?") > _MAX_SAFE_QUESTION_MARKS:
        return TriageResult(
            LABEL_SUBSTANTIVE, "multiple questions need a real answer", ["question_density"]
        )
    if _ascii_ratio(plain) < _MIN_ASCII_RATIO:
        return TriageResult(
            LABEL_SUBSTANTIVE, "non-English or non-plain-text content", ["non_ascii"]
        )

    # 4. Safe subset requires a positive match — unknown intent stays gated.
    safe_hits = [f"safe:{name}" for name, pattern in _SAFE_PATTERNS.items() if pattern.search(plain)]
    if safe_hits:
        return TriageResult(LABEL_SAFE_ACK, "simple inquiry matched safe subset", safe_hits)

    return TriageResult(LABEL_SUBSTANTIVE, "no safe pattern matched (fail-closed)", ["no_safe_match"])


def classify(subject, body, sender=None) -> TriageResult:
    """Classify one inbound email. NEVER raises; any failure → substantive.

    Args:
        subject: Email subject (any type; non-str degrades to substantive).
        body: Email body, plain text or HTML.
        sender: Sender address — accepted for interface stability / future
            per-sender rules; unused by the deterministic layer.

    Returns:
        TriageResult with label ``safe_ack`` or ``substantive``.
    """
    try:
        if not isinstance(subject, str) or not isinstance(body, str):
            return TriageResult(
                LABEL_SUBSTANTIVE, "non-string input (fail-closed)", ["bad_input_type"]
            )
        return _evaluate_rules(subject, body)
    except Exception as exc:  # noqa: BLE001 — fail-to-substantive is the contract
        return TriageResult(
            LABEL_SUBSTANTIVE, f"classifier error (fail-closed): {exc}", ["classifier_error"]
        )
