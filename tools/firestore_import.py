"""
Firestore Inventory Import Tool

Imports inventory data directly to Firestore using gcloud CLI.
This bypasses the API authentication for admin operations.

Usage:
    python tools/firestore_import.py [--preview-file FILE]
    
Prerequisites:
    - gcloud CLI installed and authenticated
    - Appropriate Firestore permissions
"""

import json
import subprocess
import sys
import os
from datetime import datetime
from typing import Dict, List

PROJECT_ID = "sapphire-479610"
COLLECTION = "inventory"


def import_to_firestore(items: List[Dict]) -> Dict:
    """Import inventory items directly to Firestore using gcloud CLI."""
    stats = {"imported": 0, "updated": 0, "errors": []}
    
    for item in items:
        doc_id = item.get("id")
        if not doc_id:
            stats["errors"].append(f"Skipping item without ID: {item.get('model_name', 'unknown')}")
            continue
        
        # Prepare document data
        firestore_data = {
            "model_name": item.get("model_name"),
            "manufacturer": item.get("manufacturer"),
            "manufacturer_id": item.get("manufacturer_id"),
            "plan_id": item.get("plan_id"),
            "year": item.get("year", 2026),
            "is_new": item.get("is_new", True),
            "bedrooms": item.get("bedrooms", 0),
            "bathrooms": item.get("bathrooms", 0),
            "sqft": item.get("sqft", 0),
            "msrp": item.get("msrp", 0),
            "sale_price": item.get("sale_price", item.get("msrp", 0)),
            "status": item.get("status", "AVAILABLE"),
            "serial_number": item.get("serial_number"),
            "sections": item.get("sections"),
            "condition": item.get("condition", "New" if item.get("is_new") else "Used"),
            "image_url": item.get("image_url") or item.get("hero_image"),
            "hero_image": item.get("hero_image"),
            "photos": item.get("photos", []),
            "floorplan_url": item.get("floorplan_url"),
            "description": item.get("description"),
            "features": item.get("features", []),
            "location": item.get("location", "San Antonio, TX"),
            "date_added": item.get("date_added") or datetime.now().isoformat(),
            "source_url": item.get("source_url"),
            "last_synced": datetime.now().isoformat(),
        }
        
        # Convert to JSON for gcloud command
        json_data = json.dumps(firestore_data)
        
        # Build gcloud command
        cmd = [
            "gcloud", "firestore", "documents", "set",
            f"projects/{PROJECT_ID}/databases/(default)/documents/{COLLECTION}/{doc_id}",
            f"--data='{json_data}'",
            "--project", PROJECT_ID
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Check if document was created or updated
                if "created" in result.stderr.lower():
                    stats["imported"] += 1
                    print(f"✓ Created: {item.get('model_name', doc_id)}")
                else:
                    stats["updated"] += 1
                    print(f"✓ Updated: {item.get('model_name', doc_id)}")
            else:
                error_msg = f"Failed to import {doc_id}: {result.stderr}"
                stats["errors"].append(error_msg)
                print(f"✗ Error: {error_msg}")
                
        except subprocess.TimeoutExpired:
            stats["errors"].append(f"Timeout importing {doc_id}")
            print(f"✗ Timeout: {doc_id}")
        except Exception as e:
            stats["errors"].append(f"Exception importing {doc_id}: {e}")
            print(f"✗ Exception: {doc_id} - {e}")
    
    return stats


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Import inventory to Firestore')
    parser.add_argument('--preview-file', default=None,
                        help='Path to the preview JSON file from inventory_sync.py')
    parser.add_argument('--check-gcloud', action='store_true',
                        help='Check if gcloud is properly configured')
    
    args = parser.parse_args()
    
    # Check gcloud if requested
    if args.check_gcloud:
        try:
            result = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True,
                text=True,
                timeout=10
            )
            print(f"Current gcloud project: {result.stdout.strip()}")
            print(f"Target project: {PROJECT_ID}")
            return
        except Exception as e:
            print(f"Error checking gcloud: {e}")
            sys.exit(1)
    
    # Find the most recent preview file if not specified
    preview_file = args.preview_file
    if not preview_file:
        data_dir = "data"
        if os.path.exists(data_dir):
            files = [f for f in os.listdir(data_dir) if f.startswith("inventory_sync_preview_") and f.endswith(".json")]
            if files:
                files.sort(reverse=True)
                preview_file = os.path.join(data_dir, files[0])
    
    if not preview_file or not os.path.exists(preview_file):
        print(f"Error: Preview file not found. Run inventory_sync.py first.")
        sys.exit(1)
    
    print(f"Loading inventory from: {preview_file}")
    with open(preview_file, 'r') as f:
        items = json.load(f)
    
    print(f"Found {len(items)} items to import\n")
    
    # Confirm import
    response = input(f"Import {len(items)} items to Firestore? [y/N]: ")
    if response.lower() != 'y':
        print("Import cancelled.")
        sys.exit(0)
    
    # Import
    print("\nStarting import...\n")
    stats = import_to_firestore(items)
    
    # Summary
    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Imported: {stats['imported']}")
    print(f"Updated:  {stats['updated']}")
    print(f"Errors:   {len(stats['errors'])}")
    
    if stats['errors']:
        print("\nErrors:")
        for error in stats['errors'][:10]:
            print(f"  - {error}")
        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
