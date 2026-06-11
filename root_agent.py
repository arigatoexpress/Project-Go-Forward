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

from google.adk.agents import LlmAgent
from google.genai import types

from config_loader import (
    business_name,
    get_agent_config,
    get_model_config,
    model_name,
    product_plural,
)
from prompt_loader import render_prompt

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
        config.temperature,
        config.max_output_tokens,
        config.top_p,
        threshold,
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
        from tools import (
            book_appointment,
            cancel_appointment,
            check_available_slots,
            get_business_hours,
            get_current_datetime,
            save_lead,
            search_inventory,
        )
    except ImportError:
        from .tools import (
            book_appointment,
            cancel_appointment,
            check_available_slots,
            get_business_hours,
            get_current_datetime,
            save_lead,
            search_inventory,
        )

    instruction = render_prompt("sales_agent")

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
            save_lead,
        ],
    )


def _create_service_agent() -> LlmAgent:
    """Create the Service Agent for warranty and support."""
    try:
        from tools import (
            analyze_defect_image,
            check_warranty_status,
            generate_customer_email,
            generate_service_ticket,
            generate_work_order_pdf,
        )
    except ImportError:
        from .tools import (
            analyze_defect_image,
            check_warranty_status,
            generate_customer_email,
            generate_service_ticket,
            generate_work_order_pdf,
        )

    return LlmAgent(
        name="service_agent",
        model=model_name(),
        description=f"Warranty and Service Coordinator handling support requests at {business_name()}.",
        generate_content_config=_build_generate_content_config(),
        instruction=render_prompt("service_agent"),
        tools=[
            check_warranty_status,
            analyze_defect_image,
            generate_work_order_pdf,
            generate_service_ticket,
            generate_customer_email,
        ],
    )


def _create_root_agent() -> LlmAgent:
    """Create the root agent that routes to specialized sub-agents."""
    try:
        from tools import get_business_hours
    except ImportError:
        from .tools import get_business_hours

    sales_agent = _create_sales_agent()
    service_agent = _create_service_agent()

    planner = _build_planner()

    kwargs = dict(
        name="root_agent",
        model=model_name(),
        description=f"Front desk receptionist and router for {business_name()}, directing customers to specialized agents.",
        generate_content_config=_build_generate_content_config(),
    )
    if planner:
        kwargs["planner"] = planner

    kwargs["instruction"] = render_prompt("root_agent")

    kwargs["sub_agents"] = [sales_agent, service_agent]
    kwargs["tools"] = [get_business_hours]

    return LlmAgent(**kwargs)


# Export the root agent for ADK
root_agent = _create_root_agent()
