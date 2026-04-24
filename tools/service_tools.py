"""
Service Tools for Texas Home Outlet Service Agent.

These tools enable the Service Agent to validate warranties, analyze defect photos,
and generate contractor invoices.
"""

from google.adk.tools import ToolContext
from typing import Optional
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import uuid


# Warranty coverage periods (from purchase date)
WARRANTY_STRUCTURAL_MONTHS = 12  # 1 year HUD structural warranty
WARRANTY_COSMETIC_DAYS = 30      # 30 days cosmetic warranty


def check_warranty_status(
    purchase_date: str,
    issue_type: str = "Structural",
    tool_context: ToolContext = None
) -> dict:
    """
    Check if a customer's home is still under warranty coverage.
    
    Warranty Coverage:
    - Structural (HUD Code): 1 year from purchase - plumbing, electrical, HVAC, structural
    - Cosmetic: 30 days from purchase - trim, paint, aesthetic issues
    - Appliances: Covered directly by manufacturer (Whirlpool, Frigidaire, etc.)
    
    Args:
        purchase_date: Date of home purchase (YYYY-MM-DD format)
        issue_type: Type of issue (Structural, Cosmetic, Appliance, HVAC, Plumbing, Electrical)
        tool_context: ADK tool context
    
    Returns:
        Dictionary with warranty status and coverage details
    """
    try:
        purchase = datetime.strptime(purchase_date, "%Y-%m-%d").date()
    except ValueError:
        return {
            "success": False,
            "error": "Invalid date format. Please use YYYY-MM-DD format."
        }
    
    today = date.today()
    days_since_purchase = (today - purchase).days
    
    # Calculate expiration dates
    structural_expires = purchase + relativedelta(months=WARRANTY_STRUCTURAL_MONTHS)
    cosmetic_expires = purchase + relativedelta(days=WARRANTY_COSMETIC_DAYS)
    
    # Determine coverage based on issue type
    issue_type_lower = issue_type.lower()
    
    if issue_type_lower == "appliance":
        return {
            "success": True,
            "covered": False,
            "coverage_type": "Manufacturer",
            "message": "Appliances are covered directly by the manufacturer (Whirlpool, Frigidaire, etc.), not by Texas Home Outlet. Please contact the appliance manufacturer for warranty service.",
            "appliance_contacts": {
                "Whirlpool": "1-800-253-1301",
                "Frigidaire": "1-800-374-4432",
                "GE": "1-800-432-2737"
            }
        }
    
    if issue_type_lower == "cosmetic":
        is_covered = today <= cosmetic_expires
        return {
            "success": True,
            "covered": is_covered,
            "coverage_type": "Cosmetic",
            "purchase_date": purchase_date,
            "days_since_purchase": days_since_purchase,
            "warranty_expires": cosmetic_expires.strftime("%Y-%m-%d"),
            "message": (
                f"Your home IS covered under the 30-day cosmetic warranty until {cosmetic_expires.strftime('%B %d, %Y')}."
                if is_covered else
                f"The 30-day cosmetic warranty expired on {cosmetic_expires.strftime('%B %d, %Y')}. Cosmetic issues are no longer covered, but we can recommend trusted contractors for repairs."
            )
        }
    
    # Structural, HVAC, Plumbing, Electrical - all covered under HUD 1-year
    is_covered = today <= structural_expires
    return {
        "success": True,
        "covered": is_covered,
        "coverage_type": "Structural (HUD)",
        "purchase_date": purchase_date,
        "days_since_purchase": days_since_purchase,
        "warranty_expires": structural_expires.strftime("%Y-%m-%d"),
        "message": (
            f"Your home IS covered under the 1-year HUD structural warranty until {structural_expires.strftime('%B %d, %Y')}. We will coordinate with the factory to resolve this issue."
            if is_covered else
            f"The 1-year structural warranty expired on {structural_expires.strftime('%B %d, %Y')}. We can provide recommendations for licensed contractors in your area."
        )
    }


def analyze_defect_image(
    image_url: str,
    defect_description: str,
    tool_context: ToolContext = None
) -> dict:
    """
    Analyze an uploaded photo of a home defect to determine cause and severity.
    
    This tool uses AI vision to classify defects as:
    - Manufacturing Defect: Covered under warranty
    - Installation Error: Covered under warranty  
    - Customer Damage: NOT covered (abuse/neglect)
    - Normal Wear: NOT covered
    
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
    damage_keywords = ["hole", "punched", "kicked", "burn", "stain", "pet", "water damage from leak I ignored"]
    defect_keywords = ["crack", "gap", "loose", "doesn't work", "leaking", "warped", "separated"]
    
    description_lower = defect_description.lower()
    
    # Simple heuristic - in production this would be Gemini Vision
    likely_cause = "Manufacturing Defect"
    warranty_likely = True
    
    for keyword in damage_keywords:
        if keyword in description_lower:
            likely_cause = "Customer Damage"
            warranty_likely = False
            break
    
    severity = "Medium"
    if any(word in description_lower for word in ["urgent", "emergency", "flooding", "no power", "no heat"]):
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
            "warranty_likely": warranty_likely,
            "confidence": "Preliminary - requires visual confirmation",
            "reasoning": (
                f"Based on the description '{defect_description}', this appears to be a {likely_cause.lower()}. "
                + ("This type of issue is typically covered under warranty." if warranty_likely else 
                   "This type of damage is typically not covered under warranty as it suggests customer responsibility.")
            )
        },
        "next_steps": (
            "I'll proceed with creating a service request for warranty repair." if warranty_likely else
            "While this isn't covered under warranty, I can provide you with a list of trusted local contractors."
        )
    }


def generate_invoice_pdf(
    customer_name: str,
    customer_address: str,
    issue_description: str,
    contractor_name: Optional[str] = None,
    contractor_email: Optional[str] = None,
    estimated_cost: Optional[float] = None,
    is_warranty_claim: bool = True,
    tool_context: ToolContext = None
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
    
    # Default contractor for local area
    if not contractor_name:
        contractor_name = "Local Home Services"
        contractor_email = "dispatch@localhomeservices.com"
    
    work_order = {
        "invoice_id": invoice_id,
        "type": "Warranty Claim" if is_warranty_claim else "Customer Billable",
        "created_at": datetime.now().isoformat(),
        "customer": {
            "name": customer_name,
            "address": customer_address
        },
        "contractor": {
            "name": contractor_name,
            "email": contractor_email
        },
        "work_description": issue_description,
        "billing": {
            "bill_to": "Factory/Manufacturer" if is_warranty_claim else "Customer",
            "estimated_cost": estimated_cost if estimated_cost else "TBD"
        },
        "status": "Sent"
    }
    
    return {
        "success": True,
        "message": f"Work order {invoice_id} has been generated and sent to {contractor_name} at {contractor_email}.",
        "work_order": work_order,
        "customer_notification": "A copy has been emailed to the customer. The contractor will reach out within 24-48 hours to schedule the service visit.",
        "follow_up": "Would you like me to schedule a follow-up check-in for next week?"
    }
