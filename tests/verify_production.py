import requests
import uuid
import json
import sys
import os

# Configuration
PROD_URL = "https://tho-agent-691674245427.us-central1.run.app"
BASE_URL = os.environ.get("AGENT_API_URL", PROD_URL)

def run_query(query_text, verify_lambda=None):
    session_id = str(uuid.uuid4())
    print(f"\n[TEST] query: '{query_text}'")

    payload = {
        "userId": f"test_user_{session_id[:8]}",
        "sessionId": session_id,
        "newMessage": {
            "role": "user",
            "parts": [{"text": query_text}]
        }
    }

    try:
        start_time = requests.post(f"{BASE_URL}/run", json=payload, timeout=30)
        response = start_time.json()

        # text field might be nested differently depending on ADK response structure
        # typically response['text'] or response['parts'][0]['text']
        print(f"[STATUS] {start_time.status_code}")

        if start_time.status_code != 200:
            print(f"[FAIL] Status code {start_time.status_code}")
            return False

        print(f"[RESPONSE RAW] {json.dumps(response)[:200]}...") # Log beginning of response

        if verify_lambda:
            if verify_lambda(response):
                print("[PASS] Verification check passed.")
                return True
            else:
                print("[FAIL] Verification check failed.")
                return False
        return True

    except Exception as e:
        print(f"[ERROR] {e}")
        return False

def verify_inventory(response):
    # Check if response contains "bedroom" or specific home details
    text = str(response)
    return "bedroom" in text.lower() or "price" in text.lower() or "sq ft" in text.lower()

def verify_appointment(response):
    # Check for appointment-related terms
    text = str(response)
    return "appointment" in text.lower() or "schedule" in text.lower() or "book" in text.lower() or "visit" in text.lower()

def main():
    print(f"Starting Functional Verification against {BASE_URL}...")

    # 1. Health/Greeting
    print("\n--- Test 1: Greeting ---")
    run_query("Hello, are you there?")

    # 2. Inventory Search
    print("\n--- Test 2: Inventory Search ---")
    run_query("Show me 3 bedroom double wide homes under 150k", verify_inventory)

    # 3. Appointment Booking
    print("\n--- Test 3: Appointment Booking ---")
    run_query("I'd like to schedule a visit to see your homes this weekend", verify_appointment)

if __name__ == "__main__":
    main()
