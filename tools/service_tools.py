"""
Service Tools for Texas Home Outlet Service Agent.

These tools enable the Service Agent to validate warranties, analyze defect photos,
and generate contractor invoices.
"""

import uuid
from datetime import datetime

from google.adk.tools import ToolContext

# The owner has not confirmed warranty lengths. Customer-facing copy stays neutral.
WARRANTY_CUSTOMER_MESSAGE = "Warranty coverage varies by manufacturer, ask a manager."


def check_warranty_status(
    purchase_date: str, issue_type: str = "Structural", tool_context: ToolContext = None
) -> dict:
    """
    Record a purchase date without stating a coverage length.

    Args:
        purchase_date: Date of home purchase (YYYY-MM-DD format)
        issue_type: Type of issue (Structural, Cosmetic, Appliance, HVAC, Plumbing, Electrical)
        tool_context: ADK tool context

    Returns:
        Dictionary with a neutral warranty message. ``covered`` stays null.
    """
    try:
        datetime.strptime(purchase_date, "%Y-%m-%d")
    except ValueError:
        return {"success": False, "error": "Invalid date format. Please use YYYY-MM-DD format."}

    return {
        "success": True,
        "covered": None,
        "coverage_type": "unconfirmed",
        "purchase_date": purchase_date,
        "issue_type": issue_type,
        "message": WARRANTY_CUSTOMER_MESSAGE,
    }


def analyze_defect_image(
    image_url: str, defect_description: str, tool_context: ToolContext = None
) -> dict:
    """
    Analyze an uploaded photo of a home defect to determine cause and severity.

    This tool records a preliminary cause. It does not decide warranty coverage.

    Args:
        image_url: URL or path to the defect image
        defect_description: Customer's description of the issue
        tool_context: ADK tool context

    Returns:
        Dictionary with analysis results
    """
    # In production, this would call Gemini Vision for actual image analysis
    # For now, we return a structured response template

    analysis_id = f"ANALYSIS-{uuid.uuid4().hex[:8].upper()}"

    # Keywords that might suggest customer damage
    damage_keywords = [
        "hole",
        "punched",
        "kicked",
        "burn",
        "stain",
        "pet",
        "water damage from leak I ignored",
    ]

    description_lower = defect_description.lower()

    # Simple heuristic - in production this would be Gemini Vision
    likely_cause = "Manufacturing Defect"

    for keyword in damage_keywords:
        if keyword in description_lower:
            likely_cause = "Customer Damage"
            break

    severity = "Medium"
    if any(
        word in description_lower
        for word in ["urgent", "emergency", "flooding", "no power", "no heat"]
    ):
        severity = "Critical"
    elif any(word in description_lower for word in ["minor", "small", "cosmetic"]):
        severity = "Low"

    return {
        "success": True,
        "analysis_id": analysis_id,
        "image_received": image_url,
        "description_analyzed": defect_description,
        "results": {
            "likely_cause": likely_cause,
            "severity": severity,
            "warranty_likely": None,
            "confidence": "Preliminary - requires visual confirmation",
            "reasoning": (
                f"Based on the description '{defect_description}', this appears to be a {likely_cause.lower()}. "
                f"{WARRANTY_CUSTOMER_MESSAGE}"
            ),
        },
        "next_steps": WARRANTY_CUSTOMER_MESSAGE,
    }


def generate_invoice_pdf(
    customer_name: str,
    customer_address: str,
    issue_description: str,
    contractor_name: str | None = None,
    contractor_email: str | None = None,
    estimated_cost: float | None = None,
    is_warranty_claim: bool = True,
    tool_context: ToolContext = None,
) -> dict:
    """
    Generate a work order / invoice PDF for contractor dispatch.

    For warranty claims, the invoice goes to the factory.
    For out-of-warranty work, it goes to the contractor for customer billing.

    Args:
        customer_name: Customer's full name
        customer_address: Service address
        issue_description: Description of the work needed
        contractor_name: Assigned contractor's name
        contractor_email: Contractor's email for notification
        estimated_cost: Estimated repair cost (for non-warranty)
        is_warranty_claim: Whether this is a warranty claim
        tool_context: ADK tool context

    Returns:
        Dictionary with invoice details and confirmation
    """
    invoice_id = f"WO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

    work_order = {
        "invoice_id": invoice_id,
        "type": "Warranty Claim" if is_warranty_claim else "Customer Billable",
        "created_at": datetime.now().isoformat(),
        "customer": {"name": customer_name, "address": customer_address},
        "contractor": {"name": contractor_name or "", "email": contractor_email or ""},
        "work_description": issue_description,
        "billing": {
            "bill_to": "Factory/Manufacturer" if is_warranty_claim else "Customer",
            "estimated_cost": estimated_cost,
        },
        "status": "Created",
    }

    if contractor_name:
        created_message = f"Work order {invoice_id} has been created for {contractor_name}."
    else:
        created_message = (
            f"Work order {invoice_id} has been created. "
            "No contractor is assigned until a manager names one."
        )

    return {
        "success": True,
        "message": created_message,
        "work_order": work_order,
        "customer_notification": WARRANTY_CUSTOMER_MESSAGE,
        "follow_up": "Ask a manager before telling the customer a schedule.",
    }
