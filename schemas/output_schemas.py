"""
Structured Output Schemas — Pydantic BaseModel definitions for ADK output_schema.

Using ADK's output_schema ensures the agent returns guaranteed JSON structure
instead of unpredictable free-form text. Pass these classes to LlmAgent's
output_schema parameter.

Reference: Google ADK docs - Structuring Data
"""

from pydantic import BaseModel, Field


class ProductSearchResult(BaseModel):
    """Schema for structured product/listing search results."""

    id: str = Field(description="Unique identifier for the product/listing")
    model_name: str = Field(description="Product or model name")
    manufacturer: str = Field(description="Manufacturer or brand name")
    classification: str = Field(description="Product category or classification")
    specs: dict[str, str] = Field(description="Key specifications as key-value pairs")
    pricing: dict[str, str] = Field(description="Pricing info with display_price")
    image_url: str | None = Field(default=None, description="Primary image URL")
    gallery_images: list[str] = Field(default=[], description="List of additional image URLs")
    detail_url: str | None = Field(default=None, description="Link to full detail page")
    match_score: float | None = Field(
        default=None, description="How well this matches the customer's criteria (0-1)"
    )


class SearchResponse(BaseModel):
    """Structured response from the sales agent when searching inventory."""

    query_summary: str = Field(description="Summary of what the customer is looking for")
    total_matches: int = Field(description="Total number of matching products found")
    results: list[ProductSearchResult] = Field(description="List of matching products")
    suggestions: list[str] = Field(
        default=[], description="Additional suggestions or recommendations"
    )


class LeadInfo(BaseModel):
    """Schema for capturing lead/contact information from conversations."""

    name: str | None = Field(default=None, description="Customer's full name")
    email: str | None = Field(default=None, description="Customer's email address")
    phone: str | None = Field(default=None, description="Customer's phone number")
    interest: str = Field(description="What the customer is interested in")
    budget_range: str | None = Field(default=None, description="Customer's budget range")
    preferred_specs: dict[str, str] = Field(default={}, description="Preferred specifications")
    urgency: str = Field(
        default="medium", description="How urgently they need the product: low, medium, high"
    )
    notes: str = Field(default="", description="Additional notes from the conversation")


class AppointmentRequest(BaseModel):
    """Schema for structured appointment booking."""

    customer_name: str = Field(description="Customer's name")
    contact_method: str = Field(description="Phone number or email")
    preferred_date: str | None = Field(default=None, description="Preferred date (YYYY-MM-DD)")
    preferred_time: str | None = Field(default=None, description="Preferred time")
    purpose: str = Field(description="Purpose of the appointment")
    products_of_interest: list[str] = Field(default=[], description="Product IDs they want to see")


class ServiceRequest(BaseModel):
    """Schema for structured service/warranty requests."""

    customer_name: str = Field(description="Customer's name")
    product_id: str = Field(description="Product or serial number")
    purchase_date: str | None = Field(default=None, description="When the product was purchased")
    issue_description: str = Field(description="Description of the issue")
    issue_category: str = Field(description="Category: warranty, repair, maintenance, other")
    severity: str = Field(default="medium", description="Severity: low, medium, high, critical")
    photos_provided: bool = Field(default=False, description="Whether customer provided photos")


class RoutingDecision(BaseModel):
    """Schema for the root agent's routing decision."""

    target_agent: str = Field(description="Agent to route to: sales_agent, service_agent, or self")
    confidence: float = Field(description="Confidence in routing decision (0-1)")
    reasoning: str = Field(description="Brief explanation of why this agent was chosen")
    customer_intent: str = Field(description="What the customer is trying to accomplish")
