"""
Lead Name Backfill — dry-run script that extracts customer names from chat
sessions and proposes patches for leads whose name is empty / 'Unknown'.

Usage (dry-run — default, no Firestore writes):
    python tools/lead_name_backfill.py

Apply patches (writes only if confidence > 0.8 AND user confirms):
    python tools/lead_name_backfill.py --apply
    python tools/lead_name_backfill.py --apply --yes      # skip interactive prompt

Options:
    --output FILE       Write JSONL report to FILE as well as stdout
    --confidence FLOAT  Minimum confidence to apply (default 0.8)
    --llm               Enable LLM fallback for messages with no regex match
    --limit N           Max leads to process (default: all)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime

from pydantic import BaseModel, Field

# ── project root on path ──────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from structured_logging import StructuredLogger  # noqa: E402

logger = StructuredLogger("lead-name-backfill")


# ──────────────────────────────────────────────────────────────────────────────
# Output schema
# ──────────────────────────────────────────────────────────────────────────────


class LeadNamePatch(BaseModel):
    lead_id: str
    current_name: str | None
    proposed_name: str
    source_session_id: str
    source_message_excerpt: str
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ──────────────────────────────────────────────────────────────────────────────
# Regex-based name extraction  (zero LLM cost)
# ──────────────────────────────────────────────────────────────────────────────

# Matches 1-3 Title-Cased words: "John", "John Smith", "Mary Jane Watson".
# (?-i:...) disables the re.IGNORECASE flag locally so that [A-Z] really
# means uppercase-only — preventing lowercase words like "looking" from matching.
_NAME_TOKEN = r"(?-i:([A-Z][a-z]+(?: [A-Z][a-z]+){0,2}))"

# Ordered highest → lowest confidence; first match wins per message.
_NAME_PATTERNS: list[tuple[float, re.Pattern]] = [
    (0.95, re.compile(rf"\bmy name is {_NAME_TOKEN}", re.IGNORECASE)),
    (0.93, re.compile(rf"\bmy name'?s {_NAME_TOKEN}", re.IGNORECASE)),
    (0.90, re.compile(rf"\b(?:hi|hello|hey),?\s+i(?:'m| am) {_NAME_TOKEN}", re.IGNORECASE)),
    (0.88, re.compile(rf"\bi(?:'m| am) {_NAME_TOKEN}", re.IGNORECASE)),
    (0.85, re.compile(rf"\bthis is {_NAME_TOKEN}", re.IGNORECASE)),
    (0.82, re.compile(rf"\bcall me {_NAME_TOKEN}", re.IGNORECASE)),
    (0.78, re.compile(rf"\bname(?:'s| is) {_NAME_TOKEN}", re.IGNORECASE)),
]

# Title-cased tokens that are common words, not names.
_STOP_NAMES: set = {
    "I",
    "Me",
    "My",
    "We",
    "Us",
    "He",
    "She",
    "They",
    "You",
    "It",
    "The",
    "A",
    "An",
    "And",
    "Or",
    "But",
    "For",
    "Yes",
    "No",
    "Hi",
    "Hello",
    "Hey",
    "Thanks",
    "Thank",
    "Ok",
    "Okay",
    "Texas",
    "Home",
    "Outlet",
    "Looking",
    "Just",
    "Actually",
    "Well",
    "So",
    "Also",
    "Sure",
    "Great",
    "Good",
    "Need",
    "Want",
    "Like",
    "Please",
    "Here",
    "There",
    "New",
    "Used",
    "Single",
    "Double",
    "Wide",
    "Not",
}


def extract_name_from_messages(
    messages: list[dict],
) -> tuple[str, float, str] | None:
    """
    Scan user-role messages for a customer name using regex patterns.

    Returns ``(name, confidence, excerpt)`` for the first strong match, or
    ``None`` if no name is found.  Only 'user' role messages are scanned.
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = msg.get("text", "")
        for confidence, pattern in _NAME_PATTERNS:
            m = pattern.search(text)
            if not m:
                continue
            name = m.group(1).strip()
            parts = name.split()
            # Reject stop-words and degenerate single-char tokens.
            if any(p in _STOP_NAMES for p in parts):
                continue
            if len(parts) == 1 and len(parts[0]) <= 2:
                continue
            # Build a short excerpt around the match.
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 20)
            excerpt = (
                ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
            )
            return (name, confidence, excerpt)
    return None


async def extract_name_with_llm(messages: list[dict]) -> tuple[str, float, str] | None:
    """
    LLM fallback: ask Gemini to find the customer's name in the conversation.
    Only called when regex extraction yields nothing and --llm flag is set.
    Returns ``(name, confidence, excerpt)`` or ``None``.
    """
    # Build transcript from user messages only (cheapest + avoids agent text)
    user_lines = [
        f"User: {msg['text']}"
        for msg in messages
        if msg.get("role") == "user" and msg.get("text", "").strip()
    ]
    if not user_lines:
        return None

    transcript = "\n".join(user_lines[-15:])  # last 15 user turns

    prompt = (
        "Read the following customer chat transcript and extract the customer's full name "
        "if they mentioned it. Return ONLY a JSON object with keys 'name' (string or null) "
        "and 'excerpt' (the verbatim snippet where the name appears, or null). "
        "Do NOT guess, fabricate, or infer names. If no name is clearly stated, return null for name.\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY valid JSON. No explanation."
    )

    try:
        from google import genai  # type: ignore

        client = genai.Client()
        response = client.models.generate_content(
            model=os.environ.get("THO_BACKFILL_MODEL", "gemini-2.0-flash-001"),
            contents=prompt,
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        name = data.get("name")
        excerpt = data.get("excerpt") or ""
        if name and len(name.strip()) >= 2:
            return (name.strip(), 0.72, excerpt[:120])
    except ImportError:
        logger.warning("google-genai not installed; LLM fallback unavailable")
    except Exception as exc:
        logger.error("LLM name extraction failed", error=str(exc))
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _is_unknown_name(name: str | None) -> bool:
    """Return True when the lead name is blank / a placeholder."""
    if not name:
        return True
    return name.strip().lower() in {"unknown", "anonymous", "guest", "user", "n/a", ""}


def _messages_to_dicts(messages) -> list[dict]:
    """
    Normalise ChatMessage objects (or plain dicts) to ``[{role, text}]`` dicts.
    """
    result = []
    for m in messages:
        if isinstance(m, dict):
            result.append({"role": m.get("role", ""), "text": m.get("text", "")})
        else:
            result.append({"role": getattr(m, "role", ""), "text": getattr(m, "text", "")})
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ──────────────────────────────────────────────────────────────────────────────


async def collect_patches(
    use_llm: bool = False,
    limit: int | None = None,
) -> list[LeadNamePatch]:
    """
    Read-only pass over Firestore:
      1. List leads whose name is empty / Unknown.
      2. For each lead, fetch its linked chat session (by session_id, then by user_id).
      3. Extract a customer name from the session messages.
      4. Yield a LeadNamePatch record for each successful extraction.
    """
    from chat_history import ChatHistory
    from lead_management import LeadManager

    lead_mgr = LeadManager()
    chat_hist = ChatHistory()

    logger.info("Fetching leads from Firestore…")
    all_leads = await lead_mgr.list_leads(limit=limit or 500)

    unknown_leads = [ld for ld in all_leads if _is_unknown_name(ld.name)]
    logger.info(
        "Found unknown-name leads",
        total=len(all_leads),
        unknown=len(unknown_leads),
    )

    patches: list[LeadNamePatch] = []

    for lead in unknown_leads:
        logger.debug("Processing lead", lead_id=lead.lead_id, session_id=lead.session_id)

        session = None

        # Primary lookup: by session_id stored on the lead
        if lead.session_id:
            try:
                session = await chat_hist.get_session(lead.session_id)
            except Exception as exc:
                logger.warning(
                    "Session lookup failed",
                    lead_id=lead.lead_id,
                    session_id=lead.session_id,
                    error=str(exc),
                )

        # Fallback: find sessions whose user_id matches the lead's user_id
        if session is None and lead.user_id:
            try:
                sessions = await chat_hist.get_user_sessions(lead.user_id, limit=3)
                if sessions:
                    # Pick the session with the most messages
                    session = max(sessions, key=lambda s: len(s.messages))
            except Exception as exc:
                logger.warning(
                    "User-session lookup failed",
                    lead_id=lead.lead_id,
                    user_id=lead.user_id,
                    error=str(exc),
                )

        if session is None:
            logger.debug("No session found for lead", lead_id=lead.lead_id)
            continue

        messages = _messages_to_dicts(session.messages)
        if not messages:
            logger.debug("Session has no messages", session_id=session.session_id)
            continue

        # Regex extraction first
        result = extract_name_from_messages(messages)

        # LLM fallback if enabled and regex found nothing
        if result is None and use_llm:
            logger.debug("Falling back to LLM for session", session_id=session.session_id)
            result = await extract_name_with_llm(messages)

        if result is None:
            logger.debug(
                "No name found in session",
                lead_id=lead.lead_id,
                session_id=session.session_id,
            )
            continue

        name, confidence, excerpt = result
        patch = LeadNamePatch(
            lead_id=lead.lead_id,
            current_name=lead.name,
            proposed_name=name,
            source_session_id=session.session_id,
            source_message_excerpt=excerpt,
            confidence=round(confidence, 3),
        )
        patches.append(patch)
        logger.info(
            "Name extracted",
            lead_id=lead.lead_id,
            proposed=name,
            confidence=confidence,
            session_id=session.session_id,
        )

    return patches


async def apply_patches(
    patches: list[LeadNamePatch],
    min_confidence: float = 0.8,
    skip_confirm: bool = False,
) -> int:
    """
    Write approved patches to Firestore.  Skips patches below min_confidence.
    Returns the number of leads updated.
    """
    from lead_management import LeadManager

    eligible = [p for p in patches if p.confidence >= min_confidence]
    logger.info(
        "Applying patches",
        total=len(patches),
        eligible=len(eligible),
        min_confidence=min_confidence,
    )

    if not eligible:
        print("No patches meet the confidence threshold — nothing to apply.", file=sys.stderr)
        return 0

    if not skip_confirm:
        print(f"\n{len(eligible)} lead(s) will be updated:")
        for p in eligible:
            print(
                f"  [{p.lead_id}] {p.current_name!r} → {p.proposed_name!r}  (conf={p.confidence})"
            )
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted — no changes written.")
            return 0

    lead_mgr = LeadManager()
    applied = 0

    for patch in eligible:
        try:
            lead = await lead_mgr.get_lead(patch.lead_id)
            if lead is None:
                logger.warning("Lead not found during apply", lead_id=patch.lead_id)
                continue
            lead.name = patch.proposed_name
            await lead_mgr.update_lead(lead)
            logger.info("Lead name updated", lead_id=patch.lead_id, name=patch.proposed_name)
            applied += 1
        except Exception as exc:
            logger.error("Failed to update lead", lead_id=patch.lead_id, error=str(exc))

    return applied


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Dry-run: extract customer names from chat history and propose lead patches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write proposed names to Firestore (requires confirmation unless --yes)",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation when --apply is set",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        help="Also write the JSONL report to FILE",
    )
    p.add_argument(
        "--confidence",
        type=float,
        default=0.8,
        metavar="FLOAT",
        help="Minimum confidence to apply (default: 0.8)",
    )
    p.add_argument(
        "--llm",
        action="store_true",
        help="Enable LLM fallback for sessions where regex found nothing",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max number of leads to process",
    )
    return p


async def _main(args: argparse.Namespace) -> int:
    patches = await collect_patches(use_llm=args.llm, limit=args.limit)

    # Emit JSONL to stdout
    for patch in patches:
        print(patch.model_dump_json())

    # Optionally write to file
    if args.output:
        with open(args.output, "w") as fh:
            for patch in patches:
                fh.write(patch.model_dump_json() + "\n")
        logger.info("Report written", path=args.output, count=len(patches))

    summary = {
        "total_patches": len(patches),
        "high_confidence": sum(1 for p in patches if p.confidence >= args.confidence),
        "dry_run": not args.apply,
    }
    print(json.dumps({"summary": summary}), file=sys.stderr)

    if args.apply:
        applied = await apply_patches(
            patches,
            min_confidence=args.confidence,
            skip_confirm=args.yes,
        )
        print(json.dumps({"applied": applied}), file=sys.stderr)

    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
