from typing import Any

from pydantic import BaseModel, Field


class DocumentRequest(BaseModel):
    """Request schema for generating a document."""

    document_type: str = Field(
        ..., description="Type of document to generate (e.g., 'sales_contract', 'credit_app')"
    )
    sale_id: str | None = Field(None, description="ID of the Sale to populate data from")
    customer_id: str | None = Field(None, description="ID of the Customer to populate data from")
    inventory_id: str | None = Field(None, description="ID of the Inventory to populate data from")
    override_data: dict[str, str] | None = Field(
        default={}, description="Manual overrides for form fields"
    )


class GenerateDocumentRequest(BaseModel):
    """Request schema for the generic document generation endpoint."""

    template_name: str = Field(
        ..., description="PDF template filename (e.g., 'TMHA_SalesContract.pdf')"
    )
    data: dict[str, Any] = Field(..., description="Business data fields to populate the template")
    session_id: str | None = Field(
        None, description="Chat session ID for pre-population from conversation"
    )
    customer_email: str | None = Field(
        None,
        description="If provided, the generated document is auto-emailed to this address.",
    )
    customer_name: str | None = Field(
        None,
        description="Customer name used in the document delivery email greeting.",
    )


class GeneratePacketRequest(BaseModel):
    """Request schema for generating a merged closing packet."""

    packet_name: str = Field(
        ..., description="Packet name from field_map.json (e.g., 'standard_closing')"
    )
    data: dict[str, Any] = Field(
        ..., description="Business data fields shared across all templates in the packet"
    )
    session_id: str | None = Field(
        None, description="Chat session ID for pre-population from conversation"
    )
    customer_email: str | None = Field(
        None,
        description="If provided, the generated packet is auto-emailed to this address.",
    )
    customer_name: str | None = Field(
        None,
        description="Customer name used in the packet delivery email greeting.",
    )


class TemplateInfo(BaseModel):
    """Response schema for template listing."""

    template_name: str
    display_name: str
    category: str
    description: str
    required_fields: list[str]
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
    buyer_phone: str | None = None

    # Home Info
    manufacturer: str
    model: str
    year: str | None = None
    no_of_sections: str | None = None
    serial_number_1: str
    serial_number_2: str | None = None
    label_number_1: str | None = None  # HUD Label
    label_number_2: str | None = None
    width: str | None = None
    length: str | None = None

    # Pricing
    sales_price: float
    down_payment: float = 0.0
    unpaid_balance: float

    # Financing
    finance_charge: float | None = None
    apr: float | None = None
    total_payments: float | None = None
    total_sale_price: float | None = None
    interest_rate: float | None = None

    # Formatting helpers
    def get_formatted_price(self) -> str:
        return f"{self.sales_price:,.2f}"

    def get_formatted_down_payment(self) -> str:
        return f"{self.down_payment:,.2f}"
