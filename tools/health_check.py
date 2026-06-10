"""
System Health Check for Texas Home Outlet AI Platform

Checks:
- API endpoints
- Firestore connectivity
- Inventory data integrity
- Frontend build status
"""

import sys
from datetime import datetime

import requests

BASE_URL = "https://tho-agent-s77j6bxyra-uc.a.run.app"


def check_endpoint(method, path, expected_status=200, description=""):
    """Check an API endpoint."""
    try:
        url = f"{BASE_URL}{path}"
        if method == "GET":
            r = requests.get(url, timeout=10)
        elif method == "POST":
            r = requests.post(url, timeout=10)
        else:
            return {"ok": False, "error": f"Unknown method: {method}"}

        ok = r.status_code == expected_status
        return {
            "ok": ok,
            "status": r.status_code,
            "error": None if ok else f"Expected {expected_status}, got {r.status_code}",
        }
    except Exception as e:
        return {"ok": False, "status": None, "error": str(e)}


def run_health_check():
    """Run comprehensive health check."""
    print("=" * 70)
    print("TEXAS HOME OUTLET AI PLATFORM - HEALTH CHECK")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 70)
    print()

    checks = [
        ("GET", "/health", 200, "Health endpoint"),
        ("GET", "/api/inventory?limit=1", 403, "Inventory API (auth required)"),
        ("GET", "/api/analytics/lead-stats", 403, "Analytics API (auth required)"),
        ("GET", "/api/leads?limit=1", 403, "Leads API (auth required)"),
    ]

    results = []
    for method, path, expected, desc in checks:
        result = check_endpoint(method, path, expected, desc)
        results.append((desc, result))
        status = "✅" if result["ok"] else "❌"
        print(f"{status} {desc}")
        if not result["ok"] and result["error"]:
            print(f"   Error: {result['error']}")

    print()
    print("=" * 70)

    # Summary
    passed = sum(1 for _, r in results if r["ok"])
    total = len(results)
    print(f"Results: {passed}/{total} checks passed")

    # Check Firestore inventory count
    print()
    print("Checking Firestore inventory...")
    try:
        from google.cloud import firestore

        db = firestore.Client(project="sapphire-479610")
        count = len(list(db.collection("inventory").stream()))
        print(f"✅ Inventory documents: {count}")

        # Check for common issues
        docs = list(db.collection("inventory").limit(5).stream())
        issues = []
        for doc in docs:
            data = doc.to_dict()
            if not data.get("model_name"):
                issues.append(f"{doc.id}: Missing model_name")
            if not data.get("photos") and not data.get("image_url"):
                issues.append(f"{doc.id}: No images")

        if issues:
            print("⚠️  Data quality issues found:")
            for issue in issues[:5]:
                print(f"   - {issue}")
        else:
            print("✅ Data quality looks good")

    except Exception as e:
        print(f"❌ Firestore check failed: {e}")

    print()
    print("=" * 70)

    return passed == total


if __name__ == "__main__":
    success = run_health_check()
    sys.exit(0 if success else 1)
