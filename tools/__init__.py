# Tools Package — lazy imports to support running without Google ADK
try:
    from .crm_tools import (
        book_appointment,
        cancel_appointment,
        check_available_slots,
        get_business_hours,
        get_current_datetime,
        save_lead,
    )
    from .inventory_tools import search_inventory
    from .marketing_tools import (
        analyze_content_performance,
        generate_ad_image,
        generate_content_script,
        get_inventory_for_ads,
        get_trending_content_ideas,
        schedule_social_post,
    )
    from .service_tools import analyze_defect_image, check_warranty_status, generate_invoice_pdf
except ImportError:
    pass  # ADK not available — running in standalone/local mode

from .document_engine import (
    generate_document,
    generate_packet,
    get_template_fields,
    list_available_packets,
    list_available_templates,
)
from .document_tools import (
    generate_customer_email,
    generate_service_ticket,
    generate_work_order_pdf,
)

__all__ = [
    "search_inventory",
    "check_warranty_status",
    "analyze_defect_image",
    "generate_invoice_pdf",
    "book_appointment",
    "get_business_hours",
    "save_lead",
    "check_available_slots",
    "cancel_appointment",
    "get_current_datetime",
    "generate_work_order_pdf",
    "generate_service_ticket",
    "generate_customer_email",
    "generate_document",
    "generate_packet",
    "list_available_templates",
    "list_available_packets",
    "get_template_fields",
    "generate_content_script",
    "get_trending_content_ideas",
    "schedule_social_post",
    "analyze_content_performance",
    "generate_ad_image",
    "get_inventory_for_ads",
]
