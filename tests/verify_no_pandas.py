
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
print("Initial modules:", len(sys.modules))

from tools import inventory_tools

print("After import inventory_tools:", len(sys.modules))

# Check if pandas is loaded
if 'pandas' in sys.modules:
    print("FAILURE: pandas IS loaded!")
else:
    print("SUCCESS: pandas is NOT loaded.")

# Test functionality
print("Testing search_inventory...")
result = inventory_tools.search_inventory(max_budget=100000)
print(f"Found {result.get('count')} homes.")

if result.get('count') > 0:
    print("SUCCESS: Functionality verified.")
else:
    print("FAILURE: No homes found (check inventory.json path).")
