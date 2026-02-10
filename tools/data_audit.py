"""
Data Quality Audit Script for House Orders Spreadsheet

This script audits the inventory data to ensure all valid records are being loaded.
"""

import pandas as pd
import os
import sys

def audit_inventory_data():
    """Audit the House Orders spreadsheet and report statistics."""
    
    # Find the spreadsheet
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "..", "data", "House Orders.xlsx"),
        os.path.join(os.path.dirname(__file__), "data", "House Orders.xlsx"),
        "data/House Orders.xlsx",
        "my_config_agent/data/House Orders.xlsx",
    ]
    
    xlsx_path = None
    for path in possible_paths:
        if os.path.exists(path):
            xlsx_path = path
            break
    
    if not xlsx_path:
        print(f"❌ ERROR: Data file not found in any of these locations:")
        for p in possible_paths:
            print(f"  - {p}")
        return
    
    print(f"✅ Found data file: {xlsx_path}")
    print(f"   File size: {os.path.getsize(xlsx_path):,} bytes\n")
    
    # Load the spreadsheet
    try:
        df = pd.read_excel(xlsx_path, sheet_name="Everybody Else", header=1)
        print(f"📊 Total rows in spreadsheet: {len(df)}")
    except Exception as e:
        print(f"❌ ERROR reading spreadsheet: {e}")
        return
    
    # Audit statistics
    total_rows = len(df)
    valid_records = 0
    skipped_no_serial = 0
    skipped_header = 0
    skipped_no_model = 0
    skipped_no_digits = 0
    zero_price_records = 0
    valid_price_records = 0
    
    valid_rows = []
    skipped_rows = []
    
    for idx, row in df.iterrows():
        serial = row.get("Serial #")
        model = row.get("Model")
        msrp = row.get("MSRP")
        
        # Check 1: Serial number exists
        if pd.isna(serial):
            skipped_no_serial += 1
            skipped_rows.append((idx, "No serial number", row))
            continue
        
        serial_str = str(serial).strip()
        
        # Check 2: Not a header row
        if serial_str in ["Serial #", "nan", ""] or "Section" in serial_str or "Approval" in serial_str:
            skipped_header += 1
            skipped_rows.append((idx, "Header/separator row", row))
            continue
        
        # Check 3: Serial should have digits
        if not any(c.isdigit() for c in serial_str):
            skipped_no_digits += 1
            skipped_rows.append((idx, "Serial has no digits", row))
            continue
        
        # Check 4: Valid model
        if model == "Model" or pd.isna(model):
            skipped_no_model += 1
            skipped_rows.append((idx, "Invalid model", row))
            continue
        
        # Valid record
        valid_records += 1
        valid_rows.append((idx, serial_str, str(model), msrp))
        
        # Track pricing
        try:
            price = float(msrp) if msrp and not pd.isna(msrp) else 0
            if price > 0:
                valid_price_records += 1
            else:
                zero_price_records += 1
        except:
            zero_price_records += 1
    
    # Print summary
    print(f"\n📈 AUDIT SUMMARY")
    print(f"=" * 60)
    print(f"✅ Valid records loaded:     {valid_records:>4} ({valid_records/total_rows*100:.1f}%)")
    print(f"⚠️  Zero/missing price:       {zero_price_records:>4}")
    print(f"💰 Valid price:              {valid_price_records:>4}")
    print(f"\n❌ SKIPPED RECORDS")
    print(f"   No serial number:         {skipped_no_serial:>4}")
    print(f"   Header/separator:         {skipped_header:>4}")
    print(f"   Serial has no digits:     {skipped_no_digits:>4}")
    print(f"   Invalid model:            {skipped_no_model:>4}")
    print(f"   TOTAL SKIPPED:            {len(skipped_rows):>4} ({len(skipped_rows)/total_rows*100:.1f}%)")
    
    # Sample valid records
    print(f"\n📋 SAMPLE VALID RECORDS (first 5):")
    for idx, serial, model, msrp in valid_rows[:5]:
        print(f"   Row {idx+2}: {serial} | {model[:30]:<30} | ${msrp if not pd.isna(msrp) else 'N/A'}")
    
    # Sample skipped records
    if skipped_rows:
        print(f"\n⚠️  SAMPLE SKIPPED RECORDS (first 5):")
        for idx, reason, row in skipped_rows[:5]:
            serial = row.get("Serial #", "N/A")
            model = row.get("Model", "N/A")
            print(f"   Row {idx+2}: {reason} | Serial: {serial} | Model: {model}")
    
    # Look for suspicious patterns
    print(f"\n🔍 DATA QUALITY CHECKS")
    
    # Check for duplicate serials
    serials = [s for _, s, _, _ in valid_rows]
    duplicates = [s for s in set(serials) if serials.count(s) > 1]
    if duplicates:
        print(f"   ⚠️  Found {len(duplicates)} duplicate serial numbers")
        for dup in duplicates[:3]:
            print(f"      - {dup}")
    else:
        print(f"   ✅ No duplicate serial numbers")
    
    # Check for missing dimensions
    missing_dims = 0
    for _, serial, model, _ in valid_rows:
        import re
        if not re.search(r'\d+x\d+', model):
            missing_dims += 1
    print(f"   {'⚠️' if missing_dims > 0 else '✅'} Missing dimensions: {missing_dims}/{valid_records}")
    
    # Check for missing bed/bath info
    missing_bb = 0
    for _, serial, model, _ in valid_rows:
        import re
        if not re.search(r'\d+/\d+', model):
            missing_bb += 1
    print(f"   {'⚠️' if missing_bb > 0 else '✅'} Missing bed/bath: {missing_bb}/{valid_records}")
    
    print(f"\n✅ Audit complete!")
    print(f"   Recommendation: {'All good!' if valid_records > 50 and len(skipped_rows) < 20 else 'Review skipped records above.'}")

if __name__ == "__main__":
    audit_inventory_data()
