import sys
import os

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from my_config_agent.caching import cache_get, cache_set
import time

def test_local_caching():
    print("Testing Local In-Memory Caching...")
    
    # Set a value
    cache_set("test_key", {"foo": "bar"}, ttl_seconds=2)
    print("Set 'test_key' to {'foo': 'bar'} with 2s TTL")
    
    # Get it back
    val = cache_get("test_key")
    if val == {"foo": "bar"}:
        print("✅ SUCCESS: Retrieved value from local cache")
    else:
        print(f"❌ FAILURE: Expected {{'foo': 'bar'}}, got {val}")
        
    # Wait for expiration
    print("Waiting 3 seconds for expiration...")
    time.sleep(3)
    
    val = cache_get("test_key")
    if val is None:
        print("✅ SUCCESS: Value expired correctly")
    else:
        print(f"❌ FAILURE: Value should be None, got {val}")

if __name__ == "__main__":
    test_local_caching()
