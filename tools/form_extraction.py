"""
Form Extraction — AI-powered extraction of form field data from chat conversation history.
Uses Gemini to parse conversation transcripts and extract relevant field values.
PII fields are explicitly excluded from the extraction prompt.
"""

import logging
import os
from typing import Any

from config.field_map_loader import get_fields_for_template

logger = logging.getLogger(__name__)


async def extract_form_data_from_session(
    session_id: str,
    template_name: str,
    runner=None,
) -> dict[str, Any]:
    """
    Extract form field data from a chat conversation session.

    Args:
        session_id: The ADK session ID
        template_name: The PDF template name to extract fields for
        runner: Optional ADK runner instance (for accessing session history)

    Returns:
        Dict with 'extracted_data' containing field values found in conversation
    """
    # Get the field definitions for this template (excluding PII fields)
    template_fields = get_fields_for_template(template_name)
    if not template_fields:
        return {"extracted_data": {}, "message": "Template not found"}

    # Filter out PII fields — they must never be sent to LLM
    safe_fields = {}
    for field_name, defn in template_fields.items():
        if defn.get("pii", False):
            continue
        safe_fields[field_name] = defn

    if not safe_fields:
        return {"extracted_data": {}, "message": "No extractable fields"}

    # Try to get conversation history
    conversation_text = await _get_conversation_text(session_id, runner)
    if not conversation_text:
        return {"extracted_data": {}, "message": "No conversation history found"}

    # Build the extraction prompt
    field_list = "\n".join(
        [
            f"- {name}: {defn.get('label', name)} (type: {defn.get('type', 'string')})"
            for name, defn in safe_fields.items()
        ]
    )

    prompt = f"""Extract form field values from the following conversation transcript.
Only extract values that are explicitly mentioned or clearly implied in the conversation.
Return a JSON object with field names as keys and extracted values as values.
Only include fields where you found a value. Do not guess or fabricate values.
Do NOT extract any SSN, date of birth, income, or other personal financial information.

Fields to look for:
{field_list}

Conversation:
{conversation_text}

Return ONLY a valid JSON object with the extracted field values. No explanation, no markdown."""

    try:
        from google import genai

        client = genai.Client()
        response = client.models.generate_content(
            model=os.environ.get("THO_EXTRACTION_MODEL", "gemini-2.0-flash-001"),
            contents=prompt,
        )

        # Parse the response
        import json

        response_text = response.text.strip()
        # Remove markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        extracted = json.loads(response_text)

        # Validate extracted fields are in our safe list
        validated = {}
        for key, value in extracted.items():
            if key in safe_fields and value:
                validated[key] = str(value)

        logger.info(f"Extracted {len(validated)} fields from session {session_id}")
        return {
            "extracted_data": validated,
            "message": f"Extracted {len(validated)} fields from conversation",
        }

    except ImportError:
        logger.warning("google-genai not available for form extraction")
        return {
            "extracted_data": {},
            "message": "AI extraction not available (genai not installed)",
        }
    except Exception as e:
        logger.error(f"Form extraction failed: {e}")
        return {"extracted_data": {}, "message": f"Extraction failed: {str(e)}"}


async def _get_conversation_text(session_id: str, runner=None) -> str | None:
    """
    Retrieve conversation text from ADK session or conversation memory.
    Returns a formatted transcript string.
    """
    # Try ADK session first
    if runner:
        try:
            session = await runner.session_service.get_session(
                app_name="root_agent",
                user_id=f"web_user_{session_id}",
                session_id=session_id,
            )
            if session and session.events:
                lines = []
                for event in session.events[-20:]:  # Last 20 events
                    if hasattr(event, "content") and event.content:
                        role = getattr(event, "author", "unknown")
                        if hasattr(event.content, "parts"):
                            text = " ".join(
                                p.text for p in event.content.parts if hasattr(p, "text")
                            )
                            if text.strip():
                                lines.append(f"{role}: {text}")
                if lines:
                    return "\n".join(lines)
        except Exception as e:
            logger.debug(f"ADK session retrieval failed: {e}")

    # Fallback: try conversation memory from Firestore
    try:
        from conversation_memory import ConversationMemory

        memory = ConversationMemory()
        context = memory.get_context(session_id)
        if context and hasattr(context, "recent_messages"):
            return "\n".join(context.recent_messages[-20:])
    except Exception as e:
        logger.debug(f"Conversation memory retrieval failed: {e}")

    return None
