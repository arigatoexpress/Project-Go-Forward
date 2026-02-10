
import pandas as pd
import json
import os
import re

def convert_excel_to_json():
    # Define paths
    base_dir = os.path.dirname(os.path.dirname(__file__)) # Up one level from tools/ to my_config_agent/
    excel_path = os.path.join(base_dir, "data", "House Orders.xlsx")
    json_path = os.path.join(base_dir, "data", "inventory.json")
    
    print(f"Reading from: {excel_path}")
    
    if not os.path.exists(excel_path):
        print("Excel file not found!")
        return
        
    df = pd.read_excel(excel_path, sheet_name="Everybody Else", header=1)
    
    inventory = []
    
    for _, row in df.iterrows():
        serial = row.get("Serial #")
        if pd.isna(serial):
            continue
        
        serial_str = str(serial).strip()
        if serial_str in ["Serial #", "nan", ""]:
            continue
        
        if "Section" in serial_str or "Approval" in serial_str or "Days out" in serial_str or "wks" in serial_str:
            continue
            
        if not any(c.isdigit() for c in serial_str):
            continue
            
        model = str(row.get("Model", ""))
        if model in ["Model", "nan"] or pd.isna(row.get("Model")) or model.strip() == "":
            continue
            
        msrp = row.get("MSRP")
        if pd.isna(msrp):
            msrp = 0
            
        # Parse dimensions
        specs = {"beds": None, "baths": None, "sq_ft": None}
        dim_match = re.search(r'(\d+)x(\d+)', model)
        if dim_match:
            width = int(dim_match.group(1))
            length = int(dim_match.group(2))
            specs["sq_ft"] = width * length
            
        bb_match = re.search(r'(\d+)/(\d+)', model)
        if bb_match:
            specs["beds"] = int(bb_match.group(1))
            specs["baths"] = int(bb_match.group(2))
            
        # Classification
        if dim_match:
            classification = "Double Wide" if int(dim_match.group(1)) >= 28 else "Single Wide"
        else:
            classification = "Unknown"
            
        # Price
        try:
            price_value = float(msrp) if msrp else 0
        except:
            price_value = 0
            
        if price_value < 50000:
            price_tier = "Under $50k"
        elif price_value < 75000:
            price_tier = "$50k-$75k"
        elif price_value < 100000:
            price_tier = "$75k-$100k"
        elif price_value < 150000:
            price_tier = "$100k-$150k"
        else:
            price_tier = "$150k+"

        # Invoice Amount
        invoice_amt = row.get("Invoice Amount")
        try:
            invoice_amount = float(invoice_amt) if invoice_amt and not pd.isna(invoice_amt) and str(invoice_amt) != "Invoice Amount" else None
        except (ValueError, TypeError):
            invoice_amount = None

        inventory.append({
            "id": str(serial),
            "serial_number": str(serial),
            "manufacturer": str(row.get("Manufacturing Plant", "")) if not pd.isna(row.get("Manufacturing Plant")) else "Unknown",
            "model_name": model,
            "classification": classification,
            "status": "Available",
            "specs": specs,
            "pricing": {
                "price_value": price_value,
                "display_price": f"${price_value:,.0f}" if price_value > 0 else "Call for Price",
                "price_tier": price_tier,
                "invoice_amount": invoice_amount
            },
            "features": [],
            "marketing_tags": []
        })
        
    print(f"Processed {len(inventory)} items.")
    
    with open(json_path, 'w') as f:
        json.dump(inventory, f, indent=2)
        
    print(f"Written JSON to: {json_path}")

if __name__ == "__main__":
    convert_excel_to_json()
