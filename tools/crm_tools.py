"""
CRM Tools for {{BUSINESS_NAME}} AI Agent.

These tools allow the agent to capture customer leads and store them
for follow-up by the sales team.
"""

from google.adk.tools import ToolContext
import json
import logging
import os
import re
from datetime import datetime

# Configure structured logging for production
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("crm_tools")

def save_lead(
    user_name: str,
    phone_number: str,
    interest_notes: str,
    tool_context: ToolContext = None
) -> dict:
    """
    Save a customer lead for sales follow-up.
    
    Use this tool when a user expresses interest in buying, financing, or visiting,
    and provides their contact details.
    
    Args:
        user_name: The customer's full name.
        phone_number: The customer's phone number.
        interest_notes: Notes about what the customer is interested in (e.g. "Looking for 3/2 double wide under $100k").
        tool_context: ADK tool context.
        
    Returns:
        Confirmation dictionary.
    """
    
    # 1. basic validation
    if not user_name or not phone_number:
        return {
            "success": False,
            "message": "Please provide both a name and phone number."
        }
        
    # clean phone number
    phone_digits = re.sub(r'\D', '', phone_number)
    if len(phone_digits) < 10:
        return {
            "success": False,
            "message": "Phone number appears invalid (too short). Please provide a 10-digit number."
        }
        
    # 2. Structure the lead data
    timestamp = datetime.utcnow().isoformat()
    lead_data = {
        "event_type": "LEAD_CAPTURE",
        "timestamp": timestamp,
        "name": user_name,
        "phone": phone_number, # Keep original format for readability
        "notes": interest_notes,
        "source": "AI_AGENT"
    }
    
    # 3. Log to stdout (Cloud Logging)
    # This ensures it is captured in production logs without needing write access to disk
    logger.info(json.dumps(lead_data))
    
    # 4. Dev/Fallback: Append to local JSON file if possible
    # In Cloud Run this is ephemeral, but useful for local testing
    try:
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        if not os.path.exists(data_dir):
            data_dir = "data" # Fallback
            
        leads_file = os.path.join(data_dir, "leads.json")
        
        # Load existing
        leads = []
        if os.path.exists(leads_file):
            try:
                with open(leads_file, 'r') as f:
                    leads = json.load(f)
            except:
                leads = []
                
        leads.append(lead_data)
        
        # Write back
        if os.access(os.path.dirname(leads_file) or '.', os.W_OK):
            with open(leads_file, 'w') as f:
                json.dump(leads, f, indent=2)
                
    except Exception as e:
        # Don't fail the tool execution if file write fails (expected in some envs)
        print(f"[CRM] Warning: Could not write to local file: {e}")

    return {
        "success": True,
        "message": f"Thanks {user_name}! I've saved your info. A sales representative will call you at {phone_number} shortly."
    }

def book_appointment(
    user_name: str,
    phone_number: str,
    preferred_datetime: str,
    notes: str = "",
    tool_context: ToolContext = None
) -> dict:
    """
    Book a showroom appointment.
    
    Args:
        user_name: Customer name
        phone_number: Customer phone
        preferred_datetime: Desired time (e.g. "Tomorrow at 2pm")
        notes: Any split requests or specific homes to see
        tool_context: ADK context
        
    Returns:
        Confirmation dict
    """
    # Reuse save_lead logic for now as they both go to the same lead funnel
    return save_lead(user_name, phone_number, f"APPOINTMENT REQUEST: {preferred_datetime}. {notes}", tool_context)

def get_business_hours(tool_context: ToolContext = None) -> dict:
    """
    Get the showroom's business hours.
    
    Returns:
        Hours schedule string
    """
    return {
        "location": "{{BUSINESS_NAME}}, 2915 FM 1960 E, {{BUSINESS_CITY}}",
        "hours": "Mon-Fri: 9am-6pm, Sat: 9am-5pm, Sun: 12pm-3pm",
        "phone": "{{BUSINESS_PHONE}}"
    }
