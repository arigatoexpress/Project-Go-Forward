"""
Document Generation Tools for Texas Home Outlet.

Generates professional PDFs for invoices, work orders, and customer communications.
"""

from io import BytesIO

try:
    from google.adk.tools import ToolContext
except ImportError:
    ToolContext = None  # Allow running without ADK for local/standalone mode
import html
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, RectangleObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from schemas.document_schemas import SalesContractForm

DOCUMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tho_documents")
_DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data/generated_docs"
)
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


def upload_to_gcs(local_path: str, filename: str) -> str | None:
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
                docs.append(
                    {
                        "filename": blob.name.split("/")[-1],
                        "size_bytes": blob.size,
                        "created_at": blob.time_created.isoformat() if blob.time_created else None,
                        "download_url": f"/api/documents/download/{blob.name.split('/')[-1]}",
                    }
                )
        return sorted(docs, key=lambda d: d.get("created_at") or "", reverse=True)
    except Exception as e:
        logging.getLogger(__name__).error(f"GCS list failed: {e}")
        return []


def create_summary_pdf(data_dict: dict[str, Any], output_path: str):
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
        clean_key = key.split(".")[-1].replace("[0]", "").replace("_", " ")
        c.drawString(50, y, f"{clean_key}: {value}")
        y -= 15

    c.save()


_logger = logging.getLogger(__name__)

TMHA_CREDITOR_PHONE_FIELDS = tuple(
    f"topmostSubform[0].Page4[0].Creditor_Phone_{index}[0]" for index in range(1, 11)
)

PDF_WIDGET_RECT_OVERRIDES = {
    "TMHA_SalesContract.pdf": {
        1: {
            # The source widget is tall enough that regenerated appearances can
            # drift into the paragraph above the line. Keep the source PDF
            # untouched and constrain only the generated appearance box.
            "Install_Address[0]": (266.794, 668.0, 573.531, 689.0),
        },
    },
}

PDF_ACROFORM_APPEARANCE_SUPPRESSED_FIELDS = {
    "TMHA_SalesContract.pdf": {
        "topmostSubform[0].Page1[0].Install_Address[0]",
        "topmostSubform[0].Page1[0].No_of_Sections[0]",
        "topmostSubform[0].Page1[0].SalePrice[0]",
        "topmostSubform[0].Page1[0].DownPmt[0]",
        "topmostSubform[0].Page1[0].Unpaid_Balance[0]",
        "topmostSubform[0].Page1[0].Total_Paid_To_Others[0]",
        "topmostSubform[0].Page1[0].Total_Unpaid_Balance[0]",
        "topmostSubform[0].Page2[0].Loan_Term[0]",
        "topmostSubform[0].Page2[0].Total_Payment[0]",
        "topmostSubform[0].Page2[0].Pmt_Start_Date[0]",
        "topmostSubform[0].Page2[0].Payment_Breakdown[0]",
        "topmostSubform[0].Page2[0].APR[0]",
        "topmostSubform[0].Page2[0].Finance_Charge[0]",
        "topmostSubform[0].Page2[0].Max_Financed[0]",
        "topmostSubform[0].Page2[0].Total_Pmts[0]",
        "topmostSubform[0].Page2[0].Total_Paid[0]",
        "topmostSubform[0].Page2[0].DownPmt[0]",
    },
}

PDF_TEXT_OVERLAYS = {
    "TMHA_SalesContract.pdf": {
        1: (
            {
                "field": "topmostSubform[0].Page1[0].Install_Address[0]",
                "x": 274,
                "y": 675,
                "max_width": 290,
                "font_size": 10,
                "clear_background": True,
            },
            {
                "field": "topmostSubform[0].Page1[0].No_of_Sections[0]",
                "x": 218,
                "y": 533,
                "max_width": 46,
                "font_size": 10,
                "align": "center",
                "clear_background": True,
            },
            {
                "field": "topmostSubform[0].Page1[0].SalePrice[0]",
                "x": 199,
                "y": 411,
                "max_width": 64,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 64,
            },
            {
                "field": "topmostSubform[0].Page1[0].DownPmt[0]",
                "x": 135,
                "y": 388,
                "max_width": 64,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 64,
            },
            {
                "field": "topmostSubform[0].Page1[0].Unpaid_Balance[0]",
                "x": 199,
                "y": 364,
                "max_width": 64,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 64,
            },
            {
                "field": "topmostSubform[0].Page1[0].Total_Paid_To_Others[0]",
                "x": 205,
                "y": 186,
                "max_width": 58,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 58,
            },
            {
                "field": "topmostSubform[0].Page1[0].Total_Unpaid_Balance[0]",
                "x": 199,
                "y": 162,
                "max_width": 64,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 64,
            },
        ),
        2: (
            {
                "field": "topmostSubform[0].Page2[0].APR[0]",
                "x": 68,
                "y": 508,
                "max_width": 57,
                "font_size": 9,
                "align": "center",
                "clear_background": True,
                "clear_width": 57,
            },
            {
                "field": "topmostSubform[0].Page2[0].Finance_Charge[0]",
                "x": 158,
                "y": 508,
                "max_width": 80,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 80,
            },
            {
                "field": "topmostSubform[0].Page2[0].Max_Financed[0]",
                "x": 247,
                "y": 508,
                "max_width": 88,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 88,
            },
            {
                "field": "topmostSubform[0].Page2[0].Total_Pmts[0]",
                "x": 342,
                "y": 508,
                "max_width": 90,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 90,
            },
            {
                "field": "topmostSubform[0].Page2[0].Total_Paid[0]",
                "x": 445,
                "y": 508,
                "max_width": 82,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 82,
            },
            {
                "field": "topmostSubform[0].Page2[0].DownPmt[0]",
                "x": 460,
                "y": 544,
                "max_width": 62,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 62,
            },
            {
                "field": "topmostSubform[0].Page2[0].Loan_Term[0]",
                "x": 58,
                "y": 447,
                "max_width": 80,
                "font_size": 9,
                "align": "center",
                "clear_background": True,
                "clear_width": 80,
            },
            {
                "field": "topmostSubform[0].Page2[0].Total_Payment[0]",
                "x": 157,
                "y": 447,
                "max_width": 80,
                "font_size": 9,
                "align": "right",
                "clear_background": True,
                "clear_width": 80,
            },
            {
                "field": "topmostSubform[0].Page2[0].Pmt_Start_Date[0]",
                "x": 249,
                "y": 447,
                "max_width": 300,
                "font_size": 9,
                "clear_background": True,
                "clear_width": 300,
            },
            {
                "field": "topmostSubform[0].Page2[0].Payment_Breakdown[0]",
                "x": 182,
                "y": 480,
                "max_width": 372,
                "font_size": 8.5,
                "clear_background": True,
                "clear_width": 372,
            },
        ),
    },
    "TDHCA_1124_Installation_Warranty.pdf": {
        1: (
            {
                "field": "THO_TDHCA1124_Installer_Contact_Name",
                "x": 340,
                "y": 213,
                "max_width": 240,
                "font_size": 10,
            },
            {
                "field": "THO_TDHCA1124_Installer_Contact_Phone",
                "x": 340,
                "y": 194,
                "max_width": 120,
                "font_size": 10,
            },
        ),
    },
}


def _ensure_parent_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _phone_digits(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits[:10]


def _prepare_pdf_fill_data(template_name: str, data_dict: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(data_dict)

    if template_name == "TMHA_SalesContract.pdf":
        existing = [str(prepared.get(field, "")).strip() for field in TMHA_CREDITOR_PHONE_FIELDS]
        already_split = len(existing) == 10 and all(
            re.fullmatch(r"\d?", value) for value in existing
        )
        if not already_split or not any(existing):
            raw_phone = prepared.get("creditor_phone") or next(
                (value for value in existing if value),
                "",
            )
            digits = _phone_digits(raw_phone)
            if digits:
                for index, field in enumerate(TMHA_CREDITOR_PHONE_FIELDS):
                    prepared[field] = digits[index] if index < len(digits) else ""

    return prepared


def _apply_pdf_widget_rect_overrides(template_name: str, writer: PdfWriter) -> None:
    page_overrides = PDF_WIDGET_RECT_OVERRIDES.get(template_name, {})
    for page_number, page in enumerate(writer.pages, start=1):
        field_overrides = page_overrides.get(page_number, {})
        if not field_overrides:
            continue
        annotations = page.get("/Annots")
        if not annotations:
            continue
        for annotation in annotations.get_object():
            widget = annotation.get_object()
            rect = field_overrides.get(str(widget.get("/T") or ""))
            if rect:
                widget[NameObject("/Rect")] = RectangleObject(rect)


def _prepare_acroform_appearance_data(
    template_name: str,
    data_dict: dict[str, Any],
) -> dict[str, Any]:
    suppressed = PDF_ACROFORM_APPEARANCE_SUPPRESSED_FIELDS.get(template_name, set())
    return {key: value for key, value in data_dict.items() if key not in suppressed}


def _draw_fit_text(
    pdf_canvas: canvas.Canvas,
    value: Any,
    *,
    x: float,
    y: float,
    max_width: float,
    font_size: float,
    clear_background: bool = False,
    clear_width: float | None = None,
    align: str = "left",
) -> None:
    text = str(value or "").strip()
    if not text:
        return

    font_name = "Helvetica"
    size = float(font_size)
    while size > 6 and pdf_canvas.stringWidth(text, font_name, size) > max_width:
        size -= 0.5

    text_width = pdf_canvas.stringWidth(text, font_name, size)
    if align == "right":
        draw_x = x + max_width - text_width
    elif align == "center":
        draw_x = x + (max_width - text_width) / 2
    else:
        draw_x = x

    if clear_background:
        background_width = clear_width if clear_width is not None else text_width
        background_x = x if clear_width is not None else draw_x
        pdf_canvas.setFillColorRGB(1, 1, 1)
        pdf_canvas.rect(
            background_x - 1,
            y - 2,
            background_width + 2,
            size + 4,
            fill=1,
            stroke=0,
        )
        pdf_canvas.setFillColorRGB(0, 0, 0)

    pdf_canvas.setFont(font_name, size)
    pdf_canvas.drawString(draw_x, y, text)


def _apply_pdf_text_overlays(
    template_name: str,
    writer: PdfWriter,
    data_dict: dict[str, Any],
) -> None:
    page_overlays = PDF_TEXT_OVERLAYS.get(template_name, {})
    for page_number, overlays in page_overlays.items():
        if page_number < 1 or page_number > len(writer.pages):
            continue

        page = writer.pages[page_number - 1]
        packet = BytesIO()
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_canvas = canvas.Canvas(packet, pagesize=(width, height))
        drew_overlay = False

        for overlay in overlays:
            value = data_dict.get(overlay["field"])
            if value is None or not str(value).strip():
                continue
            _draw_fit_text(
                overlay_canvas,
                value,
                x=overlay["x"],
                y=overlay["y"],
                max_width=overlay["max_width"],
                font_size=overlay["font_size"],
                clear_background=overlay.get("clear_background", False),
                clear_width=overlay.get("clear_width"),
                align=overlay.get("align", "left"),
            )
            drew_overlay = True

        if not drew_overlay:
            continue

        overlay_canvas.save()
        packet.seek(0)
        overlay_pdf = PdfReader(packet)
        page.merge_page(overlay_pdf.pages[0])


def fill_pdf_form(template_path: str, data_dict: dict[str, Any], output_filename: str) -> str:
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
        template_name = os.path.basename(template_path)
        fill_data = _prepare_pdf_fill_data(template_name, data_dict)
        reader = PdfReader(template_path)
        writer = PdfWriter()
        writer.append(reader)
        _apply_pdf_widget_rect_overrides(template_name, writer)

        root = reader.trailer.get("/Root", {})
        acroform = root.get("/AcroForm") if root else None

        if not acroform:
            raise ValueError("No AcroForm found")

        xfa_filled = False
        acroform_filled = False

        # XFA datasets fill — for Adobe Acrobat's XFA renderer.
        xfa = acroform.get("/XFA")
        if xfa and isinstance(xfa, ArrayObject):
            xfa_filled = _fill_xfa(writer, xfa, fill_data)

        # AcroForm fill with baked appearance streams. auto_regenerate=False
        # makes pypdf GENERATE the /AP appearance stream for each field instead
        # of setting /NeedAppearances and deferring rendering to the viewer.
        # This is critical for closing packets: PdfWriter merge strips the
        # document AcroForm/XFA/NeedAppearances, so any field without a baked
        # /AP renders blank in non-Acrobat viewers (Preview, print, mobile).
        # Baking the /AP makes every value render identically in every viewer.
        try:
            appearance_data = _prepare_acroform_appearance_data(template_name, fill_data)
            for page in writer.pages:
                writer.update_page_form_field_values(page, appearance_data, auto_regenerate=False)
            _apply_pdf_text_overlays(template_name, writer, fill_data)
            acroform_filled = True
        except Exception as e:
            _logger.warning(f"AcroForm fill failed: {e}")

        if xfa_filled or acroform_filled:
            with open(output_path, "wb") as output_stream:
                writer.write(output_stream)
            _logger.info(f"fill_pdf_form success (xfa={xfa_filled}, acroform={acroform_filled})")
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


def _fill_xfa(writer: PdfWriter, xfa: ArrayObject, data_dict: dict[str, Any]) -> bool:
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
            "<xfa:data><topmostSubform>",
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
                safe_val = (
                    str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
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
    estimated_cost: float | None = None,
    tool_context: ToolContext = None,
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
        cost_section = (
            f"""
        <div class="section">
            <div class="section-title">Estimated Cost</div>
            <p style="font-size: 24px; color: #1a5f2a;"><strong>${estimated_cost:,.2f}</strong></p>
            <p style="font-size: 12px; color: #666;">Final cost may vary based on actual work performed.</p>
        </div>
        """
            if estimated_cost
            else ""
        )

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
        cost_section=cost_section,
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
            "print": "PDF ready for printing",
        },
        "next_steps": "Contractor will contact customer within 24-48 hours to schedule service.",
    }


def generate_service_ticket(
    customer_name: str,
    customer_address: str,
    customer_phone: str,
    issue_description: str,
    issue_type: str,
    warranty_status: str,
    status: str = "Open",
    timeline: list | None = None,
    tool_context: ToolContext = None,
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
        timeline = [
            {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "event": "Ticket created via AI Service Agent",
            }
        ]

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
        timeline_items=timeline_html,
    )

    return {
        "success": True,
        "ticket_id": ticket_id,
        "document_type": "Service Ticket",
        "status": status,
        "html_content": html_content,
        "message": f"Service ticket {ticket_id} created and logged to system.",
    }


def generate_customer_email(
    customer_name: str,
    email_type: str,
    custom_content: str | None = None,
    ticket_id: str | None = None,
    appointment_details: dict | None = None,
    tool_context: ToolContext = None,
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
""",
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
""",
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
""",
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
""",
        },
    }

    template = templates.get(email_type, templates["follow_up"])

    return {
        "success": True,
        "email_type": email_type,
        "subject": template["subject"],
        "body": template["body"],
        "recipient": customer_name,
        "ready_to_send": True,
    }
