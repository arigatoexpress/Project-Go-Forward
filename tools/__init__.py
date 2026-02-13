# Tools Package
from .inventory_tools import search_inventory
from .service_tools import check_warranty_status, analyze_defect_image, generate_invoice_pdf
from .crm_tools import book_appointment, get_business_hours, save_lead, check_available_slots, cancel_appointment
from .document_tools import generate_work_order_pdf, generate_service_ticket, generate_customer_email
from .document_engine import generate_document, generate_packet, list_available_templates, list_available_packets, get_template_fields
from .marketing_tools import (
    generate_content_script,
    get_trending_content_ideas,
    schedule_social_post,
    analyze_content_performance,
    generate_ad_image,
    get_inventory_for_ads
)

__all__ = [
    # Inventory
    "search_inventory",
    # Service
    "check_warranty_status",
    "analyze_defect_image",
    "generate_invoice_pdf",
    # CRM & Scheduling
    "book_appointment",
    "get_business_hours",
    "save_lead",
    "check_available_slots",
    "cancel_appointment",
    # Documents (legacy)
    "generate_work_order_pdf",
    "generate_service_ticket",
    "generate_customer_email",
    # Document Engine (Phase 2)
    "generate_document",
    "generate_packet",
    "list_available_templates",
    "list_available_packets",
    "get_template_fields",
    # Marketing
    "generate_content_script",
    "get_trending_content_ideas",
    "schedule_social_post",
    "analyze_content_performance",
    "generate_ad_image",
    "get_inventory_for_ads"
]

