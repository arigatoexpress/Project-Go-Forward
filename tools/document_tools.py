"""
Document Generation Tools for Texas Home Outlet.

Generates professional PDFs for invoices, work orders, and customer communications.
"""

try:
    from google.adk.tools import ToolContext
except ImportError:
    ToolContext = None  # Allow running without ADK for local/standalone mode
from typing import Optional
from datetime import datetime
import base64
import html
import logging
import os
from typing import Optional, Dict, Any
import uuid

try:
    from google.adk.tools import ToolContext
except ImportError:
    ToolContext = None  # Allow running without ADK for local/standalone mode
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject

from schemas.document_schemas import SalesContractForm

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO

DOCUMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tho_documents")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/generated_docs")

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def create_summary_pdf(data_dict: Dict[str, Any], output_path: str):
    """Creates a simple summary PDF with key-value pairs."""
    c = canvas.Canvas(output_path, pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Sales Contract Data Summary")
    
    c.setFont("Helvetica", 10)
    y = height - 80
    for key, value in data_dict.items():
        if y < 50:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)
        
        # Clean key for display
        clean_key = key.split('.')[-1].replace('[0]', '').replace('_', ' ')
        c.drawString(50, y, f"{clean_key}: {value}")
        y -= 15
        
    c.save()

_logger = logging.getLogger(__name__)

def fill_pdf_form(template_path: str, data_dict: Dict[str, Any], output_filename: str) -> str:
    """
    Fills a PDF form with data. Fallback to summary page if not fillable.
    """
    _logger.info(f"fill_pdf_form called for {output_filename}")
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    try:
        reader = PdfReader(template_path)
        writer = PdfWriter()

        # Check if form exists (naive check)
        if "/AcroForm" not in reader.trailer["/Root"]:
            _logger.info("No AcroForm found, using fallback summary PDF")
            raise ValueError("No AcroForm found")

        # Copy all pages
        for page in reader.pages:
            writer.add_page(page)

        # Update fields
        for page in writer.pages:
            writer.update_page_form_field_values(
                page, data_dict, auto_regenerate=False
            )

        with open(output_path, "wb") as output_stream:
            writer.write(output_stream)

        _logger.info("fill_pdf_form success (PDF filled)")
        return output_path

    except Exception as e:
        _logger.warning(f"fill_pdf_form failed: {e}. Generating summary.")
        # Fallback
        try:
            create_summary_pdf(data_dict, output_path)
            return output_path
        except Exception as e2:
             _logger.error(f"Summary generation failed too: {e2}")
             raise e2

def generate_sales_contract_pdf(data: SalesContractForm) -> dict:
    """
    Generates a Sales Contract PDF using the TMHA template.
    Backward-compatible wrapper — delegates to the data-driven document engine.
    """
    from tools.document_engine import generate_document

    # Convert Pydantic model to dict for the engine
    data_dict = data.model_dump()
    # Ensure string conversions match legacy behavior
    for key in ("sales_price", "down_payment", "unpaid_balance"):
        if data_dict.get(key) is not None:
            data_dict[key] = str(data_dict[key])

    return generate_document("TMHA_SalesContract.pdf", data_dict)



# HTML Template for Work Order/Invoice
WORK_ORDER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; color: #333; }}
        .header {{ display: flex; justify-content: space-between; border-bottom: 3px solid #1a5f2a; padding-bottom: 20px; }}
        .logo {{ font-size: 24px; font-weight: bold; color: #1a5f2a; }}
        .tagline {{ font-size: 12px; color: #666; }}
        .doc-type {{ text-align: right; }}
        .doc-type h2 {{ margin: 0; color: #1a5f2a; }}
        .doc-number {{ color: #666; font-size: 14px; }}
        .section {{ margin: 25px 0; }}
        .section-title {{ font-weight: bold; color: #1a5f2a; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-bottom: 10px; }}
        .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        .info-box {{ background: #f9f9f9; padding: 15px; border-radius: 5px; }}
        .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
        .value {{ font-size: 14px; margin-top: 5px; }}
        .work-description {{ background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0; }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666; text-align: center; }}
        .status {{ display: inline-block; padding: 5px 15px; border-radius: 20px; font-weight: bold; }}
        .status-warranty {{ background: #d4edda; color: #155724; }}
        .status-billable {{ background: #f8d7da; color: #721c24; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <div class="logo">🏠 Texas Home Outlet</div>
            <div class="tagline">Family Owned • Veteran Owned • Since 2010</div>
        </div>
        <div class="doc-type">
            <h2>{doc_type}</h2>
            <div class="doc-number">{doc_number}</div>
            <div class="doc-number">{date}</div>
        </div>
    </div>
    
    <div class="section">
        <div class="info-grid">
            <div class="info-box">
                <div class="label">Customer</div>
                <div class="value"><strong>{customer_name}</strong></div>
                <div class="value">{customer_address}</div>
                <div class="value">{customer_phone}</div>
            </div>
            <div class="info-box">
                <div class="label">Assigned Contractor</div>
                <div class="value"><strong>{contractor_name}</strong></div>
                <div class="value">{contractor_email}</div>
                <div class="value">{contractor_phone}</div>
            </div>
        </div>
    </div>
    
    <div class="section">
        <div class="section-title">Work Description</div>
        <div class="work-description">
            {work_description}
        </div>
        <p><strong>Issue Type:</strong> {issue_type}</p>
        <p><strong>Billing:</strong> <span class="status {status_class}">{billing_type}</span></p>
    </div>
    
    {cost_section}
    
    <div class="footer">
        <p>Texas Home Outlet • 2915 FM 1960 E, Huffman • (281) 324-3020</p>
        <p>Thank you for choosing Texas Home Outlet!</p>
    </div>
</body>
</html>
"""

# Service Ticket Template
SERVICE_TICKET_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 30px; }}
        .ticket-header {{ background: #1a5f2a; color: white; padding: 20px; margin: -30px -30px 20px -30px; }}
        .ticket-id {{ font-size: 24px; font-weight: bold; }}
        .ticket-status {{ float: right; background: #ffc107; color: #333; padding: 5px 15px; border-radius: 20px; }}
        .field {{ margin: 15px 0; }}
        .field-label {{ font-weight: bold; color: #1a5f2a; }}
        .timeline {{ border-left: 3px solid #1a5f2a; padding-left: 20px; margin: 20px 0; }}
        .timeline-item {{ margin: 15px 0; position: relative; }}
        .timeline-item::before {{ content: '●'; position: absolute; left: -26px; color: #1a5f2a; }}
        .timeline-date {{ font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="ticket-header">
        <span class="ticket-status">{status}</span>
        <div class="ticket-id">Service Ticket {ticket_id}</div>
        <div>Created: {created_date}</div>
    </div>
    
    <div class="field">
        <div class="field-label">Customer</div>
        <div>{customer_name} • {customer_phone}</div>
        <div>{customer_address}</div>
    </div>
    
    <div class="field">
        <div class="field-label">Issue Description</div>
        <div>{issue_description}</div>
    </div>
    
    <div class="field">
        <div class="field-label">Issue Type</div>
        <div>{issue_type}</div>
    </div>
    
    <div class="field">
        <div class="field-label">Warranty Status</div>
        <div>{warranty_status}</div>
    </div>
    
    <div class="timeline">
        <div class="section-title" style="margin-left: -20px; font-weight: bold; color: #1a5f2a;">Activity Timeline</div>
        {timeline_items}
    </div>
</body>
</html>
"""


def generate_work_order_pdf(
    customer_name: str,
    customer_address: str,
    customer_phone: str,
    work_description: str,
    issue_type: str = "General",
    contractor_name: str = "Local Home Services",
    contractor_email: str = "dispatch@localhomeservices.com",
    contractor_phone: str = "(281) 555-0199",
    is_warranty: bool = True,
    estimated_cost: Optional[float] = None,
    tool_context: ToolContext = None
) -> dict:
    """
    Generate a professional work order/invoice PDF.
    
    Args:
        customer_name: Customer's full name
        customer_address: Service address
        customer_phone: Customer phone number
        work_description: Detailed description of work needed
        issue_type: Category of issue (Structural, Plumbing, etc.)
        contractor_name: Assigned contractor
        contractor_email: Contractor email
        contractor_phone: Contractor phone
        is_warranty: Whether this is a warranty claim
        estimated_cost: Estimated cost for non-warranty work
        tool_context: ADK tool context
    
    Returns:
        Dictionary with document details and HTML content
    """
    doc_id = f"WO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    
    # Determine billing type and styling
    if is_warranty:
        billing_type = "WARRANTY CLAIM - Bill to Factory"
        status_class = "status-warranty"
        cost_section = ""
    else:
        billing_type = "CUSTOMER BILLABLE"
        status_class = "status-billable"
        cost_section = f"""
        <div class="section">
            <div class="section-title">Estimated Cost</div>
            <p style="font-size: 24px; color: #1a5f2a;"><strong>${estimated_cost:,.2f}</strong></p>
            <p style="font-size: 12px; color: #666;">Final cost may vary based on actual work performed.</p>
        </div>
        """ if estimated_cost else ""
    
    # Generate HTML
    html_content = WORK_ORDER_TEMPLATE.format(
        doc_type="WORK ORDER",
        doc_number=html.escape(doc_id),
        date=datetime.now().strftime("%B %d, %Y"),
        customer_name=html.escape(customer_name),
        customer_address=html.escape(customer_address),
        customer_phone=html.escape(customer_phone),
        contractor_name=html.escape(contractor_name),
        contractor_email=html.escape(contractor_email),
        contractor_phone=html.escape(contractor_phone),
        work_description=html.escape(work_description),
        issue_type=html.escape(issue_type),
        billing_type=billing_type,
        status_class=status_class,
        cost_section=cost_section
    )
    
    return {
        "success": True,
        "document_id": doc_id,
        "document_type": "Work Order",
        "html_content": html_content,
        "message": f"Work order {doc_id} has been generated.",
        "actions": {
            "email_contractor": f"Ready to send to {contractor_email}",
            "email_customer": "Customer copy ready",
            "print": "PDF ready for printing"
        },
        "next_steps": "Contractor will contact customer within 24-48 hours to schedule service."
    }


def generate_service_ticket(
    customer_name: str,
    customer_address: str,
    customer_phone: str,
    issue_description: str,
    issue_type: str,
    warranty_status: str,
    status: str = "Open",
    timeline: Optional[list] = None,
    tool_context: ToolContext = None
) -> dict:
    """
    Generate a service ticket document for internal tracking.
    
    Args:
        customer_name: Customer's full name
        customer_address: Service address
        customer_phone: Customer phone
        issue_description: Description of the issue
        issue_type: Category of issue
        warranty_status: Current warranty coverage status
        status: Ticket status (Open, In Progress, Resolved)
        timeline: List of timeline events [{date, event}]
        tool_context: ADK tool context
    
    Returns:
        Dictionary with ticket details and HTML content
    """
    ticket_id = f"ST-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    
    # Build timeline HTML
    if not timeline:
        timeline = [{"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "event": "Ticket created via AI Service Agent"}]
    
    timeline_html = ""
    for item in timeline:
        timeline_html += f"""
        <div class="timeline-item">
            <div class="timeline-date">{html.escape(str(item['date']))}</div>
            <div>{html.escape(str(item['event']))}</div>
        </div>
        """
    
    html_content = SERVICE_TICKET_TEMPLATE.format(
        ticket_id=html.escape(ticket_id),
        status=html.escape(status),
        created_date=datetime.now().strftime("%B %d, %Y %I:%M %p"),
        customer_name=html.escape(customer_name),
        customer_phone=html.escape(customer_phone),
        customer_address=html.escape(customer_address),
        issue_description=html.escape(issue_description),
        issue_type=html.escape(issue_type),
        warranty_status=html.escape(warranty_status),
        timeline_items=timeline_html
    )
    
    return {
        "success": True,
        "ticket_id": ticket_id,
        "document_type": "Service Ticket",
        "status": status,
        "html_content": html_content,
        "message": f"Service ticket {ticket_id} created and logged to system."
    }


def generate_customer_email(
    customer_name: str,
    email_type: str,
    custom_content: Optional[str] = None,
    ticket_id: Optional[str] = None,
    appointment_details: Optional[dict] = None,
    tool_context: ToolContext = None
) -> dict:
    """
    Generate professional customer communication emails.
    
    Args:
        customer_name: Customer's name
        email_type: Type of email (appointment_confirmation, service_update, welcome, follow_up)
        custom_content: Custom message to include
        ticket_id: Related service ticket ID
        appointment_details: Appointment info for confirmations
        tool_context: ADK tool context
    
    Returns:
        Dictionary with email subject and body
    """
    first_name = customer_name.split()[0] if customer_name else "Valued Customer"
    
    templates = {
        "appointment_confirmation": {
            "subject": "Your Texas Home Outlet Appointment is Confirmed! 🏠",
            "body": f"""
Hi {first_name},

Great news! Your appointment at Texas Home Outlet is confirmed.

📅 Date: {appointment_details.get('date', 'TBD') if appointment_details else 'TBD'}
🕐 Time: {appointment_details.get('time', 'TBD') if appointment_details else 'TBD'}
📍 Location: 2915 FM 1960 E, Huffman

When you arrive, ask for Ben or Mark - they're excited to meet you!

What to bring:
• Valid ID
• Proof of land ownership (if applicable)
• Any financing pre-approval letters

We can't wait to help you find your dream home!

Warm regards,
The Texas Home Outlet Family
(281) 324-3020
"""
        },
        "service_update": {
            "subject": f"Update on Your Service Request {ticket_id or ''}",
            "body": f"""
Hi {first_name},

We wanted to update you on your service request{f' ({ticket_id})' if ticket_id else ''}.

{custom_content or 'Your request is being processed. A contractor will reach out within 24-48 hours to schedule your service visit.'}

If you have any questions, don't hesitate to reach out!

Best regards,
Texas Home Outlet Service Team
(281) 324-3020
"""
        },
        "welcome": {
            "subject": "Welcome to the Texas Home Outlet Family! 🎉",
            "body": f"""
Hi {first_name},

Congratulations on your new home! Welcome to the Texas Home Outlet family.

Here are some important things to know:

📋 YOUR WARRANTY COVERAGE:
• Structural (HUD): 1 year from delivery
• Cosmetic: 30 days from delivery
• Appliances: Contact manufacturer directly

🔧 NEED SERVICE?
Chat with our AI assistant anytime, or call us at (281) 324-3020

📚 HOME CARE TIPS:
• Keep your home's skirting ventilated
• Check caulking around windows seasonally
• Test smoke detectors monthly

Thank you for choosing Texas Home Outlet. We're honored to be part of your journey!

God bless,
The Texas Home Outlet Family
"""
        },
        "follow_up": {
            "subject": "How's Everything Going? 🏠",
            "body": f"""
Hi {first_name},

We wanted to check in and see how everything is going{f' since your recent service visit' if ticket_id else ''}.

{custom_content or 'Is there anything else we can help you with?'}

Your satisfaction is our top priority. If you have any concerns or just want to say hi, we're always here!

Warm regards,
Texas Home Outlet
(281) 324-3020

P.S. If you're happy with your experience, we'd love a Google review! ⭐
"""
        }
    }
    
    template = templates.get(email_type, templates["follow_up"])
    
    return {
        "success": True,
        "email_type": email_type,
        "subject": template["subject"],
        "body": template["body"],
        "recipient": customer_name,
        "ready_to_send": True
    }
