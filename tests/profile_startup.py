import os
import sys
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# project root already on path


def profile_import(module_name):
    start = time.time()
    try:
        __import__(module_name)
        end = time.time()
        print(f"Import {module_name:<30}: {end-start:.4f}s")
    except Exception as e:
        print(f"Import {module_name:<30}: FAILED ({e})")


print("--- Profiling Imports ---")
profile_import("os")
profile_import("json")
profile_import("google.genai")
profile_import("vertexai")
profile_import("google.adk")
profile_import("pandas")
profile_import("tools")  # This triggers tools/__init__.py
profile_import("root_agent")
# Add main.py directory to path to allow import
# project root already on path
profile_import("main")
profile_import("main")
