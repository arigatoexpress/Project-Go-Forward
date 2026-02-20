
import sys
import os
import json
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools import crm_tools

# Test 1: Valid Lead
print("Testing valid lead...")
result = crm_tools.save_lead(
    user_name="John Doe",
    phone_number="555-123-4567",
    interest_notes="Looking for a 3 bedroom double wide."
)
print(f"Result: {result}")

if result['success']:
    print("SUCCESS: Valid lead accepted.")
else:
    print("FAILURE: Valid lead rejected.")

# Test 2: Invalid Phone
print("\nTesting invalid phone...")
result = crm_tools.save_lead(
    user_name="Invalid Phone",
    phone_number="123",
    interest_notes="test"
)
print(f"Result: {result}")

if not result['success']:
    print("SUCCESS: Invalid phone rejected.")
else:
    print("FAILURE: Invalid phone accepted.")

# Test 3: Check local file (if writable)
try:
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    leads_file = os.path.join(data_dir, "leads.json")
    if os.path.exists(leads_file):
        with open(leads_file, 'r') as f:
            leads = json.load(f)
            last_lead = leads[-1]
            if last_lead['name'] == "John Doe":
                print("\nSUCCESS: Lead found in local JSON file.")
            else:
                print(f"\nFAILURE: Last lead was {last_lead['name']}, expected John Doe.")
    else:
        print("\nNOTE: leads.json not found (expected if data dir not writable/created).")
except Exception as e:
    print(f"\nError checking file: {e}")
