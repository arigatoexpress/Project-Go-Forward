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
import html
import logging
import os
from typing import Dict, Any
import uuid

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject

from schemas.document_schemas import SalesContractForm

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

DOCUMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tho_documents")
_DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/generated_docs")
_TMP_OUTPUT_DIR = "/tmp/generated_docs"

# Use /tmp fallback for Cloud Run (read-only filesystem)
try:
    os.makedirs(_DEFAULT_OUTPUT_DIR, exist_ok=True)
    _test = os.path.join(_DEFAULT_OUTPUT_DIR, ".write_test")
    with open(_test, "w") as f:
        f.write("ok")
    os.remove(_test)
    OUTPUT_DIR = _DEFAULT_OUTPUT_DIR
except OSError:
    os.makedirs(_TMP_OUTPUT_DIR, exist_ok=True)
    OUTPUT_DIR = _TMP_OUTPUT_DIR

# ─── GCS for durable PDF storage ────────────────────────────────────────────
_GCS_BUCKET_NAME = os.getenv("GCS_DOCUMENTS_BUCKET", "tho-secure-documents")
_gcs_client = None
_gcs_bucket = None


def _get_gcs_bucket():
    """Lazy-load GCS bucket for document storage."""
    global _gcs_client, _gcs_bucket
    if _gcs_bucket is None:
        try:
            from google.cloud import storage
            _gcs_client = storage.Client()
            _gcs_bucket = _gcs_client.bucket(_GCS_BUCKET_NAME)
        except Exception as e:
            logging.getLogger(__name__).warning(f"GCS unavailable: {e}")
            return None
    return _gcs_bucket


def upload_to_gcs(local_path: str, filename: str) -> Optional[str]:
    """Upload a generated PDF to GCS. Returns the GCS URI or None."""
    if os.getenv("THO_DISABLE_GCS_UPLOADS", "").lower() in {"1", "true", "yes"}:
        logging.getLogger(__name__).info("GCS upload disabled; skipping %s", filename)
        return None

    bucket = _get_gcs_bucket()
    if bucket is None:
        return None
    try:
        blob = bucket.blob(f"generated_docs/{filename}")
        blob.upload_from_filename(local_path, content_type="application/pdf")
        return f"gs://{_GCS_BUCKET_NAME}/generated_docs/{filename}"
    except Exception as e:
        logging.getLogger(__name__).error(f"GCS upload failed for {filename}: {e}")
        return None


def download_from_gcs(filename: str, local_path: str) -> bool:
    """Download a PDF from GCS to a local path. Returns True on success."""
    bucket = _get_gcs_bucket()
    if bucket is None:
        return False
    try:
        blob = bucket.blob(f"generated_docs/{filename}")
        if not blob.exists():
            return False
        blob.download_to_filename(local_path)
        return True
    except Exception as e:
        logging.getLogger(__name__).error(f"GCS download failed for {filename}: {e}")
        return False


def list_gcs_documents() -> list:
    """List all generated documents in GCS."""
    bucket = _get_gcs_bucket()
    if bucket is None:
        return []
    try:
        blobs = bucket.list_blobs(prefix="generated_docs/")
        docs = []
        for blob in blobs:
            if blob.name.endswith(".pdf"):
                docs.append({
                    "filename": blob.name.split("/")[-1],
                    "size_bytes": blob.size,
                    "created_at": blob.time_created.isoformat() if blob.time_created else None,
                    "download_url": f"/api/documents/download/{blob.name.split('/')[-1]}",
                })
        return sorted(docs, key=lambda d: d.get("created_at") or "", reverse=True)
    except Exception as e:
        logging.getLogger(__name__).error(f"GCS list failed: {e}")
        return []

def create_summary_pdf(data_dict: Dict[str, Any], output_path: str):
    """Creates a simple summary PDF with key-value pairs."""
    _ensure_parent_dir(output_path)
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


def _ensure_parent_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def fill_pdf_form(template_path: str, data_dict: Dict[str, Any], output_filename: str) -> str:
    """
    Fills a PDF form with data. THO templates are hybrid XFA + AcroForm.

    We fill BOTH layers so the result works everywhere:
      - XFA datasets: rendered by Adobe Acrobat's XFA engine.
      - AcroForm fields with regenerated appearances: rendered as static page
        content by every PDF viewer (Preview, Chrome, Firefox, etc.) AND
        survives merge into closing packets (PdfWriter.add_page strips XFA but
        preserves AcroForm appearance streams).

    Filling only XFA leaves the static page blank in non-Acrobat viewers and
    the merged closing packet completely empty — see the Joe Blo customer
    report (Apr 2026).
    """
    _logger.info(f"fill_pdf_form called for {output_filename}")
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    _ensure_parent_dir(output_path)

    try:
        reader = PdfReader(template_path)
        writer = PdfWriter()
        writer.append(reader)

        root = reader.trailer.get("/Root", {})
        acroform = root.get("/AcroForm") if root else None

        if not acroform:
            raise ValueError("No AcroForm found")

        xfa_filled = False
        acroform_filled = False

        # XFA datasets fill — for Adobe Acrobat's XFA renderer.
        xfa = acroform.get("/XFA")
        if xfa and isinstance(xfa, ArrayObject):
            xfa_filled = _fill_xfa(writer, xfa, data_dict)

        # AcroForm fill with appearance regeneration — bakes values into the
        # page content stream so they render in any viewer and survive merge.
        try:
            for page in writer.pages:
                writer.update_page_form_field_values(
                    page, data_dict, auto_regenerate=True
                )
            acroform_filled = True
        except Exception as e:
            _logger.warning(f"AcroForm fill failed: {e}")

        if xfa_filled or acroform_filled:
            with open(output_path, "wb") as output_stream:
                writer.write(output_stream)
            _logger.info(
                f"fill_pdf_form success (xfa={xfa_filled}, acroform={acroform_filled})"
            )
            # Durable storage: upload to GCS
            upload_to_gcs(output_path, output_filename)
            return output_path

        raise ValueError("Could not fill form with any method")

    except Exception as e:
        _logger.warning(f"fill_pdf_form failed: {e}. Generating summary.")
        try:
            create_summary_pdf(data_dict, output_path)
            upload_to_gcs(output_path, output_filename)
            return output_path
        except Exception as e2:
            _logger.error(f"Summary generation failed too: {e2}")
            raise e2


def _fill_xfa(writer: PdfWriter, xfa: ArrayObject, data_dict: Dict[str, Any]) -> bool:
    """Fill XFA form by injecting data into the datasets XML stream.

    XFA PDFs (created by Adobe LiveCycle) store form data as XML.
    We rebuild the datasets XML with our field values.
    """
    try:
        # Find the datasets stream in the XFA array
        datasets_idx = None
        for i in range(0, len(xfa), 2):
            if str(xfa[i]) == "datasets":
                datasets_idx = i + 1
                break

        if datasets_idx is None:
            return False

        # Build new datasets XML with filled data
        xml_parts = [
            '<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/">',
            '<xfa:data><topmostSubform>',
        ]

        for field_name, value in data_dict.items():
            # Strip XFA path prefixes — use just the field name
            clean_name = field_name
            if "." in clean_name:
                # Extract the actual field name from paths like
                # topmostSubform[0].Page1[0].Seller_Name[0]
                clean_name = clean_name.split(".")[-1]
                # Remove array indices like [0]
                if "[" in clean_name:
                    clean_name = clean_name.split("[")[0]

            if value is not None and str(value).strip():
                # Escape XML special chars
                safe_val = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                xml_parts.append(f"<{clean_name}>{safe_val}</{clean_name}>")
            else:
                xml_parts.append(f"<{clean_name}/>")

        xml_parts.append("</topmostSubform></xfa:data></xfa:datasets>")
        new_xml = "".join(xml_parts)

        # Replace the datasets stream in the writer's AcroForm
        writer_acroform = writer._root_object.get("/AcroForm")
        if writer_acroform:
            writer_xfa = writer_acroform.get("/XFA")
            if writer_xfa and isinstance(writer_xfa, ArrayObject):
                for i in range(0, len(writer_xfa), 2):
                    if str(writer_xfa[i]) == "datasets":
                        new_stream = DecodedStreamObject()
                        new_stream.set_data(new_xml.encode("utf-8"))
                        writer_xfa[i + 1] = new_stream
                        _logger.info(f"XFA datasets replaced with {len(data_dict)} fields")
                        return True

        return False

    except Exception as e:
        _logger.warning(f"XFA fill failed: {e}")
        return False

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

We wanted to check in and see how everything is going{' since your recent service visit' if ticket_id else ''}.

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
