import sys
import os
import json

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from structured_logging import logger
from analytics_service import get_analytics_summary

def test_analytics_logging():
    print("Testing Analytics Logging...")
    try:
        logger.log_analytics_event(
            event_type="TEST_EVENT",
            session_id="test_session_123",
            data={"foo": "bar"}
        )
        print("✅ SUCCESS: log_analytics_event executed without error (check stdout for JSON)")
    except Exception as e:
        print(f"❌ FAILURE: log_analytics_event raised {e}")

def test_analytics_service():
    print("\nTesting Analytics Service...")
    try:
        data = get_analytics_summary()
        if "overview" in data and "total_sessions" in data["overview"]:
            print("✅ SUCCESS: get_analytics_summary returned expected structure")
        else:
            print(f"❌ FAILURE: Unexpected data structure: {data.keys()}")
    except Exception as e:
        print(f"❌ FAILURE: get_analytics_service raised {e}")

if __name__ == "__main__":
    test_analytics_logging()
    test_analytics_service()
