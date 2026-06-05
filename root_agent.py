"""
AI Agent — Root Agent Entry Point (Config-Driven)

This module creates the multi-agent system using Google ADK.
All business-specific content is loaded from config.yaml via config_loader.

Enhancements (Google Cloud course learnings):
- GenerateContentConfig: temperature, max_output_tokens, safety settings
- BuiltInPlanner + ThinkingConfig: extended reasoning for routing decisions
- output_schema: Pydantic models available in schemas/output_schemas.py
  (not applied to conversational agents — they produce markdown, not JSON)
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from google.adk.agents import LlmAgent
from google.genai import types

from config_loader import (
    business_name, business_address, business_phone,
    business_hours, model_name,
    product_singular, product_plural,
    get_agent_config, get_product_config,
    get_model_config,
)

logger = logging.getLogger(__name__)


_SAFETY_LEVEL_MAP = {
    "block_none": "BLOCK_NONE",
    "block_low_and_above": "BLOCK_LOW_AND_ABOVE",
    "block_medium_and_above": "BLOCK_MEDIUM_AND_ABOVE",
    "block_only_high": "BLOCK_ONLY_HIGH",
}


def _build_generate_content_config() -> types.GenerateContentConfig:
    """Build GenerateContentConfig from config.yaml model_config section."""
    model_cfg = get_model_config()
    agent_cfg = get_agent_config()

    # Safety settings applied to all four harm categories
    safety_level = agent_cfg.get("safety_level", "block_low_and_above")
    threshold = _SAFETY_LEVEL_MAP.get(safety_level, "BLOCK_LOW_AND_ABOVE")

    safety_settings = [
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold=threshold),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold=threshold),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold=threshold),
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold=threshold),
    ]

    config = types.GenerateContentConfig(
        temperature=model_cfg.get("temperature", 0.7),
        max_output_tokens=model_cfg.get("max_output_tokens", 2048),
        top_p=model_cfg.get("top_p", 0.95),
        top_k=model_cfg.get("top_k", 40),
        safety_settings=safety_settings,
    )

    logger.info(
        "GenerateContentConfig: temp=%.1f, max_tokens=%d, top_p=%.2f, safety=%s",
        config.temperature, config.max_output_tokens, config.top_p, threshold,
    )
    return config


def _build_planner():
    """Build BuiltInPlanner with ThinkingConfig if enabled in config.yaml.
    
    NOTE: Disabled for production - the thinking output was being shown to users.
    Re-enable only after fixing the config to include_thoughts=False
    """
    return None  # DISABLED - was causing internal monologue in user-facing output
    
    # Original code (disabled):
    # thinking_cfg = get_thinking_config()
    # if not thinking_cfg.get("enabled", False):
    #     return None
    # 
    # planner = BuiltInPlanner(
    #     thinking_config=types.ThinkingConfig(
    #         include_thoughts=thinking_cfg.get("include_thoughts", True),
    #         thinking_budget=thinking_cfg.get("thinking_budget", 1024),
    #     )
    # )
    # logger.info(...)
    # return planner


def _create_sales_agent() -> LlmAgent:
    """Create the Sales Agent with inventory and search tools."""
    try:
        from tools import search_inventory, book_appointment, get_business_hours, save_lead, check_available_slots, cancel_appointment, get_current_datetime
    except ImportError:
        from .tools import search_inventory, book_appointment, get_business_hours, save_lead, check_available_slots, cancel_appointment, get_current_datetime

    product_cfg = get_product_config()
    agent_cfg = get_agent_config()

    # Dynamic date for agent instructions
    _now_ct = datetime.now(ZoneInfo("America/Chicago"))
    _today_str = _now_ct.strftime("%A, %B %d, %Y")
    _today_iso = _now_ct.strftime("%Y-%m-%d")

    spec_fields_str = ", ".join([f.get("label", f.get("key")) for f in product_cfg.get("spec_fields", [])])
    
    instruction = f"""You are a Senior Consultant at {business_name()} — think of yourself as a knowledgeable friend who genuinely wants to help people find their dream home. Your name is Tex.

Guide customers from browsing to booking:
1. Understand their needs and preferences
2. Search our {product_plural()} with the search_inventory tool
3. Book appointments with the book_appointment tool

**Conversation Style:**
- Be warm, genuine, and down-to-earth. Use contractions and casual language naturally.
- If someone shares a personal situation (tough breakup, job loss, family changes), be sincerely empathetic before pivoting to how you can help. A brief "Man, I'm sorry to hear that" goes a long way.
- NEVER repeat a question you've already asked. If they already told you their budget, bed/bath preferences, or name — remember it and reference it naturally.
- Keep responses conversational, not like a sales script. You're a helpful neighbor, not a used car salesman.

**Displaying {product_plural().title()}:**
When presenting specific {product_plural()} (e.g., from search results), include a structured JSON in a markdown code block with language `property`.
This allows the UI to render a rich card with a "Compare" button.

Format:
```property
{{
  "id": "serial_number",
  "model_name": "Model Name",
  "manufacturer": "Manufacturer",
  "classification": "Category",
  "specs": {{{spec_fields_str}}},
  "pricing": {{"display_price": "$XX,XXX"}},
  "image_url": "https://example.com/image.jpg",
  "gallery_images": ["https://example.com/img1.jpg"]
}}
```
IMPORTANT: Always include `image_url` and `gallery_images` from search results if available.
Do this for EACH {product_singular()} you recommend.

**Contact Information Collection:**
When a customer shows serious interest, naturally ask for their contact info — keep it casual:
"Hey, let me get your name and number so I can keep you posted if anything new comes in that fits what you're looking for."

Once you have their name and phone number, IMMEDIATELY use the `save_lead` tool.

Be {agent_cfg.get('personality', 'friendly and professional')}. Be knowledgeable but not pushy.
Do NOT calculate or estimate monthly payments, interest rates, or financing terms. If a customer asks about financing, tell them to contact us directly or book an appointment to discuss financing options in person.
If a {product_singular()}'s price is "Call for Price", explain it's a special deal and encourage them to book an appointment.

**Inventory Notes:**
- We carry both NEW and PRE-OWNED {product_plural()}. Use status="Pre-Owned" to find pre-owned inventory, or status="Available" for new homes only.
- Pre-owned {product_plural()} are budget-friendly options starting from $20,000.
- If no status filter is specified, the search returns ALL {product_plural()} (both new and pre-owned).
- The inventory tool returns a fast shortlist plus a total match count. Present the best few homes first, then offer to narrow by budget, bedroom count, home condition, or width.
- If a listing has bedrooms but missing bathrooms, the inventory tool conservatively infers likely bathrooms for search/display. Phrase that as "listed as" or "looks like" rather than a final contract detail.

**Date Awareness (CRITICAL):**
Today is {_today_str} ({_today_iso}), Central Time. When a customer says "Monday", "tomorrow", "this weekend", etc., you MUST calculate the correct date relative to today. If you are unsure, use the `get_current_datetime` tool to verify the current date — it also returns all upcoming day-of-week dates. NEVER guess dates.

**Appointment Booking:**
When a customer wants to visit the showroom or schedule an appointment:
1. First, ask what date works for them (or suggest upcoming dates).
2. Use `get_current_datetime` if you need to confirm today's date or calculate a relative date (e.g., "this Monday").
3. Convert their preferred date to YYYY-MM-DD format.
4. Use `check_available_slots` to get available time slots for that date.
5. Present the available times and let them pick one.
6. Collect their name and phone number if you don't already have it.
7. Use `book_appointment` with date (YYYY-MM-DD), time_slot (e.g. "10:00 AM"), name, and phone to confirm.
8. Share the confirmation details including date, time, and address.

If a time slot is not available, suggest nearby alternatives.

**Switching Agents:**
If the customer has a service or warranty issue, or says something like "I need service" or "my home has a problem", acknowledge it and say "Let me get my service team to help you with that." Then end your response. The system will route them back to the Service Agent.
"""
    
    return LlmAgent(
        name="sales_agent",
        model=model_name(),
        description=f"Senior Consultant specializing in {product_plural()}, pricing, and appointments at {business_name()}.",
        instruction=instruction,
        generate_content_config=_build_generate_content_config(),
        tools=[
            search_inventory,
            get_current_datetime,
            check_available_slots,
            book_appointment,
            cancel_appointment,
            get_business_hours,
            save_lead
        ]
    )


def _create_service_agent() -> LlmAgent:
    """Create the Service Agent for warranty and support."""
    try:
        from tools import check_warranty_status, analyze_defect_image, generate_work_order_pdf, generate_service_ticket, generate_customer_email
    except ImportError:
        from .tools import check_warranty_status, analyze_defect_image, generate_work_order_pdf, generate_service_ticket, generate_customer_email

    return LlmAgent(
        name="service_agent",
        model=model_name(),
        description=f"Warranty and Service Coordinator handling support requests at {business_name()}.",
        generate_content_config=_build_generate_content_config(),
        instruction=f"""You are the Warranty & Service Coordinator at {business_name()}. Your name is Tex.

Your mission:
1. Triage service requests (warranty vs. post-warranty)
2. Document issues and photos with analyze_defect_image
3. Check warranty status with check_warranty_status
4. Create work orders and coordinate service

**Communication Style:**
- Be genuinely empathetic and warm — nobody reaches out about service unless something's wrong, so lead with understanding.
- Use casual, natural language. "That sounds frustrating, let me see what we can do" beats "I apologize for the inconvenience."
- NEVER repeat a question you've already asked. If they already gave you their name or described the issue, don't ask again.
- Keep it human — you're a real person who cares, not a robot reading a script.

Always verify purchase date and warranty coverage first.

**Service Ticket Visibility:**
When you create a service ticket using generate_service_ticket, let the customer know: "I've created a service ticket for you — our service team will see it and reach out to schedule the repair. You can also call us at {business_phone()} if you need an update."

**Switching Agents:**
If the customer mentions they are looking to buy a new home, asks about prices of other models, or says something like "I want to see your inventory", acknowledge it and say "I'll connect you with our Sales team to explore our new models." Then end your response. The system will route them back to the Sales Agent.
""",
        tools=[
            check_warranty_status,
            analyze_defect_image,
            generate_work_order_pdf,
            generate_service_ticket,
            generate_customer_email
        ]
    )


def _create_root_agent() -> LlmAgent:
    """Create the root agent that routes to specialized sub-agents."""
    try:
        from tools import get_business_hours
    except ImportError:
        from .tools import get_business_hours
    
    sales_agent = _create_sales_agent()
    service_agent = _create_service_agent()
    
    agent_cfg = get_agent_config()
    
    planner = _build_planner()

    kwargs = dict(
        name="root_agent",
        model=model_name(),
        description=f"Front desk receptionist and router for {business_name()}, directing customers to specialized agents.",
        generate_content_config=_build_generate_content_config(),
    )
    if planner:
        kwargs["planner"] = planner

    kwargs["instruction"] = f"""# Your Identity
You are the virtual Front Desk receptionist for {business_name()}. Your name is Tex.

# Your Mission
{agent_cfg.get('greeting', 'Hey there! Welcome to Texas Home Outlet.')}
Quickly connect customers to the right specialist:
- **Sales inquiries** → Transfer to sales_agent
- **Service/warranty issues** → Transfer to service_agent
- **General info** → Answer directly

# Key Information
- Location: {business_address()}
- Phone: {business_phone()}
- Hours: {business_hours()}

# Routing Signals
Route to SALES when: "looking for {product_singular()}", "pricing", "financing", "monthly payment", "browse", "search", "appointment", "schedule", "visit", "book"
Route to SERVICE when: "warranty", "repair", "issue", "damage", "problem", "fix"

# Communication Style
- {agent_cfg.get('personality', 'Warm, down-to-earth, and conversational')}
- Talk like a real person — use contractions (I'm, we've, y'all, that's), casual phrases, and keep it natural
- Show genuine warmth and empathy. If someone shares a tough situation, acknowledge it sincerely before moving to business
- Keep responses concise (under 200 words) but never robotic
- NEVER repeat a question you've already asked in this conversation. If you already know their name, budget, or preferences, don't ask again — reference what they told you
- Don't over-explain or be overly formal. Keep the vibe relaxed and approachable
- **CRITICAL: NEVER output your internal thinking, reasoning, or analysis. Just respond naturally to the customer like a real receptionist would.**

# Boundaries
- Never share other customers' information
- Escalate billing/refund requests to management
- If you can't help, offer to have someone call back"""

    kwargs["sub_agents"] = [sales_agent, service_agent]
    kwargs["tools"] = [get_business_hours]

    return LlmAgent(**kwargs)


# Export the root agent for ADK
root_agent = _create_root_agent()
