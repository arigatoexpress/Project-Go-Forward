"""
Firebase Firestore Import Script

Imports inventory data to Firestore using Google Cloud Firestore client.

Prerequisites:
    pip install google-cloud-firestore

Usage:
    python tools/firebase_import.py
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from google.cloud import firestore
except ImportError:
    print("Error: google-cloud-firestore not installed.")
    print("Run: pip install google-cloud-firestore")
    sys.exit(1)


def import_inventory():
    """Import inventory to Firestore."""
    # Initialize Firestore client
    project_id = os.environ.get("FIRESTORE_PROJECT_ID", "sapphire-479610")
    db = firestore.Client(project=project_id)
    collection = "inventory"

    # Read import data
    data_path = Path(__file__).parent.parent / "data" / "firestore_inventory_import.json"
    with open(data_path) as f:
        inventory = json.load(f)

    doc_ids = list(inventory.keys())
    print(f"Found {len(doc_ids)} inventory items to import\n")

    imported = 0
    updated = 0
    errors = []

    for doc_id in doc_ids:
        try:
            data = inventory[doc_id]
            doc_ref = db.collection(collection).document(doc_id)

            # Check if document exists
            existing = doc_ref.get()

            if existing.exists:
                doc_ref.update(data)
                print(f"✓ Updated: {data['model_name']}")
                updated += 1
            else:
                doc_ref.set(data)
                print(f"✓ Created: {data['model_name']}")
                imported += 1

        except Exception as e:
            print(f"✗ Error importing {doc_id}: {e}")
            errors.append({"id": doc_id, "error": str(e)})

    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Created: {imported}")
    print(f"Updated: {updated}")
    print(f"Errors:  {len(errors)}")
    print("=" * 60)

    if errors:
        print("\nErrors encountered:")
        for e in errors[:10]:
            print(f"  - {e['id']}: {e['error']}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")

    return len(errors) == 0


if __name__ == "__main__":
    success = import_inventory()
    sys.exit(0 if success else 1)
