# Tools Package
from .inventory_tools import search_inventory, calculate_payment
from .service_tools import check_warranty_status, analyze_defect_image, generate_invoice_pdf
from .crm_tools import book_appointment, get_business_hours, save_lead
from .document_tools import generate_work_order_pdf, generate_service_ticket, generate_customer_email
from .marketing_tools import (
    generate_content_script,
    get_trending_content_ideas,
    schedule_social_post,
    analyze_content_performance
)

__all__ = [
    # Inventory
    "search_inventory",
    "calculate_payment",
    # Service
    "check_warranty_status",
    "analyze_defect_image",
    "generate_invoice_pdf",
    # CRM
    "book_appointment",
    "get_business_hours",
    "save_lead",
    # Documents
    "generate_work_order_pdf",
    "generate_service_ticket",
    "generate_customer_email",
    # Marketing
    "generate_content_script",
    "get_trending_content_ideas",
    "schedule_social_post",
    "analyze_content_performance"
]

