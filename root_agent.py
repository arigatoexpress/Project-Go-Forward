"""
AI Agent — Root Agent Entry Point (Config-Driven)

This module creates the multi-agent system using Google ADK.
All business-specific content is loaded from config.yaml via config_loader.
"""

from google.adk.agents import LlmAgent
import os

from config_loader import (
    business_name, business_address, business_phone,
    business_hours, agent_name, model_name,
    product_type, product_singular, product_plural,
    get_agent_config, get_product_config
)


def _create_sales_agent() -> LlmAgent:
    """Create the Sales Agent with inventory and search tools."""
    try:
        from tools import search_inventory, calculate_payment, book_appointment, get_business_hours, save_lead
    except ImportError:
        from .tools import search_inventory, calculate_payment, book_appointment, get_business_hours, save_lead

    product_cfg = get_product_config()
    agent_cfg = get_agent_config()
    
    spec_fields_str = ", ".join([f.get("label", f.get("key")) for f in product_cfg.get("spec_fields", [])])
    
    instruction = f"""You are a Senior Consultant at {business_name()} with extensive expertise in our {product_plural()}.
        
Guide customers from browsing to booking:
1. Understand their needs and preferences
2. Search our {product_plural()} with the search_inventory tool
3. Calculate payments with the calculate_payment tool
4. Book appointments with the book_appointment tool

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
  "pricing": {{"display_price": "$XX,XXX", "monthly_payment": "$XXX"}},
  "image_url": "https://example.com/image.jpg",
  "gallery_images": ["https://example.com/img1.jpg"]
}}
```
IMPORTANT: Always include `image_url` and `gallery_images` from search results if available.
Do this for EACH {product_singular()} you recommend.

**Contact Information Collection:**
When a customer shows serious interest, politely ask for their contact information:
"I'd love to help you further. May I get your name and the best way to reach you (email or phone)?"

Once you have their name and phone number, IMMEDIATELY use the `save_lead` tool.

Be {agent_cfg.get('personality', 'friendly and professional')}. Be knowledgeable but not pushy.
Never guarantee interest rates - they depend on qualification.
If a {product_singular()}'s price is "Call for Price", explain it's a special deal and encourage them to book an appointment.

**Inventory Notes:**
- We carry both NEW and PRE-OWNED {product_plural()}. Use status="Pre-Owned" to find pre-owned inventory, or status="Available" for new homes only.
- Pre-owned {product_plural()} are budget-friendly options starting from $20,000.
- If no status filter is specified, the search returns ALL {product_plural()} (both new and pre-owned).

**Switching Agents:**
If the customer has a service or warranty issue, or says something like "I need service" or "my home has a problem", acknowledge it and say "Let me get my service team to help you with that." Then end your response. The system will route them back to the Service Agent.
"""
    
    return LlmAgent(
        name="sales_agent",
        model=model_name(),
        description=f"Senior Consultant specializing in {product_plural()}, pricing, and appointments at {business_name()}.",
        instruction=instruction,
        tools=[
            search_inventory,
            calculate_payment,
            book_appointment,
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
        instruction=f"""You are the Warranty & Service Coordinator at {business_name()}.

Your mission:
1. Triage service requests (warranty vs. post-warranty)
2. Document issues and photos with analyze_defect_image
3. Check warranty status with check_warranty_status
4. Create work orders and coordinate service

Be empathetic - nobody calls about service unless they have a problem.
Always verify purchase date and warranty coverage first.

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
    
    return LlmAgent(
        name="root_agent",
        model=model_name(),
        description=f"Front desk receptionist and router for {business_name()}, directing customers to specialized agents.",
        instruction=f"""# Your Identity
You are the virtual Front Desk receptionist for {business_name()}.

# Your Mission
{agent_cfg.get('greeting', 'Welcome! How can I help you today?')}
Quickly connect customers to the right specialist:
- **Sales inquiries** → Transfer to sales_agent
- **Service/warranty issues** → Transfer to service_agent
- **General info** → Answer directly

# Key Information
- Location: {business_address()}
- Phone: {business_phone()}
- Hours: {business_hours()}

# Routing Signals
Route to SALES when: "looking for {product_singular()}", "pricing", "financing", "monthly payment", "browse", "search"
Route to SERVICE when: "warranty", "repair", "issue", "damage", "problem", "fix"

# Communication Style
- {agent_cfg.get('personality', 'Friendly and professional')}
- Concise (under 200 words)
- Patient and empathetic

# Boundaries
- Never share other customers' information
- Escalate billing/refund requests to management
- If you can't help, offer to have someone call back""",
        sub_agents=[
            sales_agent,
            service_agent
        ],
        tools=[
            get_business_hours
        ]
    )


# Export the root agent for ADK
root_agent = _create_root_agent()
