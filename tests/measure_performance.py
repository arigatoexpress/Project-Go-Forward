import time
import requests
import uuid
import statistics

import os
import sys

# Configuration
# Default to local, but allow override or easy switch to prod
DEFAULT_URL = "http://localhost:8080"
PROD_URL = "https://tho-agent-691674245427.us-central1.run.app"

# Use first arg as URL if provided, otherwise check env, otherwise default
if len(sys.argv) > 1:
    BASE_URL = sys.argv[1]
else:
    BASE_URL = os.environ.get("AGENT_API_URL", DEFAULT_URL)

print(f"Targeting API: {BASE_URL}")

NUM_REQUESTS = 5

def measure_chat_latency():
    print(f"Measuring latency for {NUM_REQUESTS} requests...")
    latencies = []
    
    session_id = str(uuid.uuid4())
    user_id = f"perf_test_{session_id[:8]}"
    
    # payload pattern
    payload = {
        "userId": user_id,
        "sessionId": session_id,
        "newMessage": {
            "role": "user",
            "parts": [{"text": "Show me 3 bedroom homes"}]
        }
    }
    
    for i in range(NUM_REQUESTS):
        start = time.time()
        try:
            # We are assuming the server is running locally. 
            # If not, this will fail connection, which is fine for the artifact creation.
            # In a real scenario, we'd ensure the server is up.
            response = requests.post(f"{BASE_URL}/run", json=payload, timeout=30)
            if response.status_code == 200:
                duration = (time.time() - start) * 1000
                latencies.append(duration)
                print(f"Request {i+1}: {duration:.2f}ms")
            else:
                print(f"Request {i+1}: Failed ({response.status_code})")
        except Exception as e:
            print(f"Request {i+1}: Error - {e}")
            
    if latencies:
        print("\n--- Results ---")
        print(f"Avg Latency: {statistics.mean(latencies):.2f}ms")
        print(f"Min Latency: {min(latencies):.2f}ms")
        print(f"Max Latency: {max(latencies):.2f}ms")
    else:
        print("\nNo successful requests to measure.")

if __name__ == "__main__":
    measure_chat_latency()
