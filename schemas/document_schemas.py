from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import date

class DocumentRequest(BaseModel):
    """Request schema for generating a document."""
    document_type: str = Field(..., description="Type of document to generate (e.g., 'sales_contract', 'credit_app')")
    sale_id: Optional[str] = Field(None, description="ID of the Sale to populate data from")
    customer_id: Optional[str] = Field(None, description="ID of the Customer to populate data from")
    inventory_id: Optional[str] = Field(None, description="ID of the Inventory to populate data from")
    override_data: Optional[Dict[str, str]] = Field(default={}, description="Manual overrides for form fields")


class GenerateDocumentRequest(BaseModel):
    """Request schema for the generic document generation endpoint."""
    template_name: str = Field(..., description="PDF template filename (e.g., 'TMHA_SalesContract.pdf')")
    data: Dict[str, Any] = Field(..., description="Business data fields to populate the template")
    session_id: Optional[str] = Field(None, description="Chat session ID for pre-population from conversation")


class GeneratePacketRequest(BaseModel):
    """Request schema for generating a merged closing packet."""
    packet_name: str = Field(..., description="Packet name from field_map.json (e.g., 'standard_closing')")
    data: Dict[str, Any] = Field(..., description="Business data fields shared across all templates in the packet")
    session_id: Optional[str] = Field(None, description="Chat session ID for pre-population from conversation")


class TemplateInfo(BaseModel):
    """Response schema for template listing."""
    template_name: str
    display_name: str
    category: str
    description: str
    required_fields: List[str]
    field_count: int

class SalesContractForm(BaseModel):
    """Data schema for TMHA Sales Contract."""
    # Buyer Info
    buyer_name: str
    buyer_address: str
    buyer_city: str
    buyer_county: str
    buyer_state: str = "TX"
    buyer_zip: str
    buyer_phone: Optional[str] = None
    
    # Home Info
    manufacturer: str
    model: str
    year: Optional[str] = None
    serial_number_1: str
    serial_number_2: Optional[str] = None
    label_number_1: Optional[str] = None # HUD Label
    label_number_2: Optional[str] = None
    width: Optional[str] = None
    length: Optional[str] = None
    
    # Pricing
    sales_price: float
    down_payment: float = 0.0
    unpaid_balance: float
    
    # Financing
    finance_charge: Optional[float] = None
    apr: Optional[float] = None
    total_payments: Optional[float] = None
    total_sale_price: Optional[float] = None
    interest_rate: Optional[float] = None
    
    # Formatting helpers
    def get_formatted_price(self) -> str:
        return f"{self.sales_price:,.2f}"

    def get_formatted_down_payment(self) -> str:
        return f"{self.down_payment:,.2f}"
