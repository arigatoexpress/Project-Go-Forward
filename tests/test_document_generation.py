import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from schemas.document_schemas import SalesContractForm
from tools.document_tools import generate_sales_contract_pdf


def test_sales_contract_generation():
    print("Testing Sales Contract Generation...")

    # Mock Data
    data = SalesContractForm(
        buyer_name="John Doe",
        buyer_address="123 Maple St",
        buyer_city="Humble",
        buyer_county="Harris",
        buyer_state="TX",
        buyer_zip="77338",
        buyer_phone="555-0199",
        manufacturer="Clayton",
        model="The Hammond",
        serial_number_1="CLW123456TXA",
        serial_number_2="CLW123456TXB",
        sales_price=125000.00,
        down_payment=5000.00,
        unpaid_balance=120000.00,
    )

    # Generate
    result = generate_sales_contract_pdf(data)

    if result["success"]:
        print(f"SUCCESS: Generated {result['filename']}")
        print(f"path: {result['file_path']}")

        if os.path.exists(result["file_path"]):
            print("Verified file exists on disk.")
        else:
            print("ERROR: File returned success but does not exist on disk.")
    else:
        print(f"FAILURE: {result['message']}")


if __name__ == "__main__":
    test_sales_contract_generation()
