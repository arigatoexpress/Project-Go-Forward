"""
Document Engine v2.0 — Domain-driven document intelligence.
Implements unified schema, computed financial fields, and backward compatibility.
"""

import json
import logging
import os
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pypdf import PdfReader, PdfWriter

from config.field_map_loader import get_field_map
from tools.document_quality import (
    enrich_document_data,
    quality_failure_response,
    validate_document_quality,
)

# Local imports
from tools.document_tools import DOCUMENTS_DIR, OUTPUT_DIR, fill_pdf_form, upload_to_gcs
from tools.drive_service import ensure_deal_folder, upload_to_drive

logger = logging.getLogger(__name__)

LEGACY_FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "buyer_name": ("person.buyer_name",),
    "buyer_address": ("person.buyer_address",),
    "buyer_city": ("person.buyer_city",),
    "buyer_county": ("person.buyer_county",),
    "buyer_state": ("person.buyer_state",),
    "buyer_zip": ("person.buyer_zip",),
    "buyer_phone": ("person.buyer_phone",),
    "buyer_email": ("person.buyer_email",),
    "co_buyer_name": ("person.co_buyer_name",),
    "manufacturer": ("product.manufacturer",),
    "model": ("product.model",),
    "serial_number_1": ("product.serial_number_1",),
    "serial_number_2": ("product.serial_number_2",),
    "label_number_1": ("product.label_number_1", "product.hud_number"),
    "label_number_2": ("product.label_number_2",),
    "sales_price": ("financial.sales_price",),
    "down_payment": ("financial.down_payment",),
    "loan_term": ("financial.loan_term",),
    "apr": ("financial.apr",),
    "interest_rate": ("financial.apr",),
    "monthly_payment": ("financial.monthly_payment",),
    "total_monthly_payment": ("financial.monthly_payment",),
    "total_payments": ("financial.total_of_payments",),
    "finance_charge": ("financial.finance_charge",),
    "unpaid_balance": ("financial.loan_amount",),
    "total_unpaid_balance": ("financial.loan_amount",),
    "max_financed": ("financial.loan_amount",),
}


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


# ─── Pydantic Models ────────────────────────────────────────────────────────


class PersonModel(BaseModel):
    buyer_name: str
    buyer_first_name: str | None = None
    buyer_last_name: str | None = None
    buyer_address: str
    buyer_city: str | None = None
    buyer_county: str | None = None
    buyer_state: str = "TX"
    buyer_zip: str | None = None
    buyer_email: str | None = None
    buyer_phone: str | None = None
    buyer_ssn: str | None = None
    buyer_dob: str | None = None
    buyer_marital_status: str | None = None
    co_buyer_name: str | None = None
    co_buyer_first_name: str | None = None
    co_buyer_last_name: str | None = None
    co_buyer_address: str | None = None
    co_buyer_ssn: str | None = None
    co_buyer_dob: str | None = None
    employer_name: str | None = None
    occupation: str | None = None
    occupation_length: str | None = None
    work_phone: str | None = None


class ProductModel(BaseModel):
    manufacturer: str
    model: str
    serial_number_1: str
    serial_number_2: str | None = None
    hud_number: str | None = None
    label_number_1: str | None = None
    label_number_2: str | None = None
    year_built: int | None = None
    year: str | None = None
    is_new: bool = True
    is_used: bool = False
    no_of_sections: str | None = None
    sq_ft: str | None = None
    wind_zone: str | None = "1"
    weight_sec_1: str | None = None
    weight_sec_2: str | None = None
    date_of_manufacture: str | None = None
    manufacturer_address: str | None = None
    manufacturer_city_state_zip: str | None = None

    @model_validator(mode="after")
    def validate_exclusion(self):
        if self.is_new and self.is_used:
            # If both are true, we default to New for legacy reasons but log it
            logger.warning("Both is_new and is_used are True. Defaulting to is_used=False.")
            self.is_used = False
        if not self.is_new and not self.is_used:
            # If neither are true, we default to New
            self.is_new = True
        return self


class FinancialModel(BaseModel):
    sales_price: Decimal = Field(default=Decimal("0"), decimal_places=2)
    down_payment: Decimal = Field(default=Decimal("0"), decimal_places=2)
    loan_term: int = Field(default=240, ge=1, le=360)
    apr: Decimal = Field(default=Decimal("7.5"), decimal_places=2)
    doc_fee: Decimal | None = None
    amt_points: Decimal | None = None
    unpaid_balance: Decimal | None = None
    total_payments: Decimal | None = None
    total_paid_to_others: Decimal | None = None

    # Computed fields
    loan_amount: Decimal | None = None
    monthly_payment: Decimal | None = None
    total_of_payments: Decimal | None = None
    finance_charge: Decimal | None = None

    @field_validator("sales_price", "down_payment", mode="before")
    @classmethod
    def default_blank_money(cls, value: Any) -> Any:
        if value is None:
            return Decimal("0")
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace("$", "").strip()
            return Decimal("0") if cleaned == "" else cleaned
        return value

    @field_validator("loan_term", mode="before")
    @classmethod
    def default_blank_loan_term(cls, value: Any) -> Any:
        if value is None:
            return 240
        if isinstance(value, str):
            cleaned = value.strip()
            return 240 if cleaned == "" else cleaned
        return value or 240

    @field_validator("apr", mode="before")
    @classmethod
    def default_blank_apr(cls, value: Any) -> Any:
        if value is None:
            return Decimal("7.5")
        if isinstance(value, str):
            cleaned = value.replace("%", "").strip()
            return Decimal("7.5") if cleaned == "" else cleaned
        return value

    @field_validator(
        "doc_fee",
        "amt_points",
        "unpaid_balance",
        "total_payments",
        "total_paid_to_others",
        mode="before",
    )
    @classmethod
    def blank_optional_money_is_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace("$", "").strip()
            return None if cleaned == "" else cleaned
        return value

    @model_validator(mode="after")
    def compute_fields(self):
        self.loan_amount = self.sales_price - self.down_payment

        # Monthly payment calculation (PMT)
        if self.loan_term <= 0:
            self.loan_term = 240

        r = (self.apr / Decimal("100")) / Decimal("12")
        if r > 0:
            power = (1 + r) ** self.loan_term
            self.monthly_payment = (self.loan_amount * r * power / (power - 1)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            self.monthly_payment = (self.loan_amount / Decimal(self.loan_term)).quantize(
                Decimal("0.01")
            )

        self.total_of_payments = (
            self.monthly_payment * self.loan_term + self.down_payment
        ).quantize(Decimal("0.01"))
        self.finance_charge = (self.total_of_payments - self.sales_price).quantize(Decimal("0.01"))
        return self


class MetaModel(BaseModel):
    generation_id: str = Field(default_factory=lambda: str(uuid4())[:12].upper())
    generation_timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    generated_by: str = "THO System"


class UnifiedPayload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    person: PersonModel
    product: ProductModel
    financial: FinancialModel
    meta: MetaModel = Field(default_factory=MetaModel)

    @classmethod
    def from_flat_payload(cls, data: dict[str, Any]) -> "UnifiedPayload":
        """
        Backward compatibility: normalizes flat v1 payloads into v2 domains.
        """
        domain_map = {
            "buyer_name": "person",
            "buyer_first_name": "person",
            "buyer_last_name": "person",
            "buyer_address": "person",
            "buyer_city": "person",
            "buyer_county": "person",
            "buyer_state": "person",
            "buyer_zip": "person",
            "buyer_email": "person",
            "buyer_phone": "person",
            "buyer_ssn": "person",
            "buyer_dob": "person",
            "buyer_marital_status": "person",
            "co_buyer_name": "person",
            "co_buyer_first_name": "person",
            "co_buyer_last_name": "person",
            "co_buyer_address": "person",
            "co_buyer_ssn": "person",
            "co_buyer_dob": "person",
            "employer_name": "person",
            "occupation": "person",
            "occupation_length": "person",
            "work_phone": "person",
            "manufacturer": "product",
            "model": "product",
            "serial_number_1": "product",
            "serial_number_2": "product",
            "hud_number": "product",
            "label_number_1": "product",
            "label_number_2": "product",
            "year": "product",
            "is_new": "product",
            "is_used": "product",
            "no_of_sections": "product",
            "sq_ft": "product",
            "wind_zone": "product",
            "weight_sec_1": "product",
            "weight_sec_2": "product",
            "date_of_manufacture": "product",
            "manufacturer_address": "product",
            "manufacturer_city_state_zip": "product",
            "sales_price": "financial",
            "down_payment": "financial",
            "loan_term": "financial",
            "apr": "financial",
            "doc_fee": "financial",
            "amt_points": "financial",
        }

        normalized = {"person": {}, "product": {}, "financial": {}, "meta": {}}
        for k, v in data.items():
            domain = domain_map.get(k)
            if domain:
                # Handle numeric string conversion
                if domain == "financial" and isinstance(v, str):
                    try:
                        v = v.replace(",", "").replace("$", "").strip()
                    except Exception:
                        pass
                normalized[domain][k] = v
            elif k in ["generation_id", "generation_timestamp", "generated_by"]:
                normalized["meta"][k] = v

        return cls(**normalized)


UnifiedPayload.model_rebuild()

# ─── Engine Logic ──────────────────────────────────────────────────────────


class DocumentEngineV2:
    def __init__(self, schema_path: str = "config/unified_schema.json"):
        self.schema_path = schema_path
        if os.path.exists(schema_path):
            with open(schema_path) as f:
                self.schema = json.load(f)
        else:
            logger.warning(f"Schema not found at {schema_path}. Using empty schema.")
            self.schema = {"domains": {}, "templates": {}, "packets": {}}

        # Load legacy schema for backward compatibility
        try:
            self.legacy_schema = get_field_map()
            logger.info(
                f"Loaded legacy schema with {len(self.legacy_schema.get('templates', {}))} templates."
            )
        except Exception as e:
            logger.error(f"Failed to load legacy schema: {e}")
            self.legacy_schema = {"templates": {}, "packets": {}}

    def _safe_filename_token(self, value: Any, default: str = "Doc", max_length: int = 50) -> str:
        raw = str(value or default)
        token = re.sub(r"[^a-zA-Z0-9_\s\-]", "", raw).strip().replace(" ", "_")
        token = token[:max_length].strip("_")
        return token or default

    def generate_document(
        self,
        template_name: str,
        data: dict[str, Any],
        output_filename: str | None = None,
        deal_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a single document using the v2 engine.
        """
        data = enrich_document_data(data)
        quality_issues = validate_document_quality(data, templates=[template_name])
        if quality_issues:
            return quality_failure_response(quality_issues)

        # 1. Normalize and Validate Payload
        try:
            if "person" in data:
                payload = UnifiedPayload(**data)
            else:
                payload = UnifiedPayload.from_flat_payload(data)
        except Exception as e:
            logger.error(f"Payload validation failed for {template_name}: {e}")
            return {"success": False, "message": f"Validation failed: {str(e)}"}

        # 2. Lookup Template (v2 priority, then legacy)
        tpl_config = self.schema["templates"].get(template_name)
        legacy_tpl_config = self.legacy_schema.get("templates", {}).get(template_name)

        if not tpl_config and not legacy_tpl_config:
            return {
                "success": False,
                "message": f"Template {template_name} not found in unified or legacy schemas.",
            }

        # If it's only in legacy, we'll use a virtual v2 config
        is_legacy_only = False
        if not tpl_config:
            is_legacy_only = True
            tpl_config = {
                "display_name": legacy_tpl_config.get("display_name", template_name),
                "category": legacy_tpl_config.get("category", "Legacy"),
                "field_map": {},  # We'll handle legacy mapping separately
                "checkbox_fields": {},
                "static_values": legacy_tpl_config.get("static_values", {}),
            }

        # 3. Verify PDF exists
        template_path = os.path.join(DOCUMENTS_DIR, template_name)
        if not os.path.exists(template_path):
            return {"success": False, "message": f"Template file not found: {template_path}"}

        # 4. Build Mapping
        pdf_data = {}
        flat_payload = {}
        for domain, model in payload.model_dump().items():
            for field, val in model.items():
                flat_payload[f"{domain}.{field}"] = val

        # Map text fields
        for domain_field, pdf_path in tpl_config.get("field_map", {}).items():
            val = flat_payload.get(domain_field)
            if val is not None:
                # Format specific types if needed
                if isinstance(val, Decimal):
                    pdf_data[pdf_path] = f"{val:,.2f}"
                else:
                    pdf_data[pdf_path] = str(val)

        # Legacy Mapping Fallback (if v2 mapping is sparse or missing)
        if legacy_tpl_config:
            # Text fields from legacy
            for pdf_path, legacy_key in legacy_tpl_config.get("field_map", {}).items():
                if pdf_path not in pdf_data:  # Don't overwrite v2 mapping if it exists
                    val = None
                    for domain_key in LEGACY_FIELD_SOURCES.get(legacy_key, ()):
                        candidate = flat_payload.get(domain_key)
                        if _has_value(candidate):
                            val = candidate
                            break

                    # If still None, try the raw legacy key from input data
                    if not _has_value(val):
                        val = data.get(legacy_key)

                    if _has_value(val):
                        if isinstance(val, Decimal | float) or (
                            isinstance(val, str)
                            and legacy_key
                            in [
                                "sales_price",
                                "down_payment",
                                "monthly_payment",
                                "total_monthly_payment",
                                "total_payments",
                                "max_financed",
                                "unpaid_balance",
                                "total_unpaid_balance",
                                "finance_charge",
                            ]
                        ):
                            try:
                                d_val = Decimal(str(val).replace("$", "").replace(",", ""))
                                pdf_data[pdf_path] = f"{d_val:,.2f}"
                            except Exception:
                                pdf_data[pdf_path] = str(val)
                        else:
                            pdf_data[pdf_path] = str(val)

            # Checkbox fields from legacy
            for pdf_path, legacy_key in legacy_tpl_config.get("checkbox_fields", {}).items():
                if pdf_path not in pdf_data:
                    val = data.get(legacy_key)
                    if isinstance(val, bool):
                        pdf_data[pdf_path] = "On" if val else "Off"
                    elif isinstance(val, str) and val.lower() in ["true", "yes", "on", "1"]:
                        pdf_data[pdf_path] = "On"

        # Static values
        pdf_data.update(tpl_config.get("static_values", {}))
        if legacy_tpl_config:
            # Merge legacy static values if not already present
            for k, v in legacy_tpl_config.get("static_values", {}).items():
                if k not in pdf_data:
                    pdf_data[k] = v

        # 5. Output Filename
        if not output_filename:
            safe_tpl = self._safe_filename_token(template_name.replace(".pdf", ""))
            safe_buyer = self._safe_filename_token(payload.person.buyer_name)
            date_str = datetime.now().strftime("%Y%m%d")
            output_filename = f"{safe_tpl}_{safe_buyer}_{date_str}.pdf"

        # 6. Fill and Store
        try:
            output_path = fill_pdf_form(template_path, pdf_data, output_filename)

            # Mirror to Drive if deal_id is provided
            drive_url = None
            current_deal_id = deal_id or data.get("deal_id")
            if current_deal_id:
                deal_folder_id = ensure_deal_folder(current_deal_id)
                if deal_folder_id:
                    drive_url = upload_to_drive(output_path, output_filename, deal_folder_id)

            return {
                "success": True,
                "file_path": output_path,
                "filename": output_filename,
                "drive_url": drive_url,
                "document_id": payload.meta.generation_id,
                "message": f"{tpl_config.get('display_name', template_name)} generated successfully",
                "intelligence": {
                    "computed": payload.financial.model_dump(),
                    "mapping": {
                        "total_fields": len(pdf_data),
                        "source": "unified_schema_v2" if not is_legacy_only else "legacy_fallback",
                    },
                },
            }
        except Exception as e:
            logger.error(f"PDF filling failed: {e}")
            return {"success": False, "message": str(e)}

    def generate_batch(
        self,
        template_names: list[str],
        data: dict[str, Any],
        merge: bool = True,
        deal_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate multiple documents and optionally merge.
        """
        data = enrich_document_data(data)
        quality_issues = validate_document_quality(data, templates=template_names)
        if quality_issues:
            response = quality_failure_response(quality_issues)
            response.update(
                {
                    "documents": [],
                    "merged": None,
                    "total": len(template_names),
                    "successful": 0,
                }
            )
            return response

        results = []
        successful_files = []

        for tpl in template_names:
            res = self.generate_document(tpl, data, deal_id=deal_id)
            template_config = (
                self.schema.get("templates", {}).get(tpl)
                or self.legacy_schema.get("templates", {}).get(tpl)
                or {}
            )
            results.append(
                {
                    "template_name": tpl,
                    "display_name": template_config.get("display_name", tpl),
                    "success": res["success"],
                    "filename": res.get("filename"),
                    "download_url": f"/api/documents/download/{res['filename']}"
                    if res.get("filename")
                    else None,
                    "message": res.get("message", ""),
                    "intelligence": res.get("intelligence"),
                }
            )
            if res["success"] and res.get("file_path"):
                successful_files.append(res["file_path"])

        merged = None
        if merge and len(successful_files) > 1:
            try:
                writer = PdfWriter()
                for fpath in successful_files:
                    reader = PdfReader(fpath)
                    for page in reader.pages:
                        writer.add_page(page)

                buyer = self._safe_filename_token(data.get("buyer_name", "Customer"))
                date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                merged_filename = f"Documents_{buyer}_{date_str}.pdf"
                merged_path = os.path.join(OUTPUT_DIR, merged_filename)

                with open(merged_path, "wb") as f:
                    writer.write(f)
                upload_to_gcs(merged_path, merged_filename)

                drive_url = None
                current_deal_id = deal_id or data.get("deal_id")
                if current_deal_id:
                    deal_folder_id = ensure_deal_folder(current_deal_id)
                    if deal_folder_id:
                        drive_url = upload_to_drive(merged_path, merged_filename, deal_folder_id)

                merged = {
                    "filename": merged_filename,
                    "download_url": f"/api/documents/download/{merged_filename}",
                    "drive_url": drive_url,
                    "page_count": len(writer.pages),
                    "document_count": len(successful_files),
                    "intelligence": {
                        "batch_id": str(uuid4())[:8],
                        "summary": f"Unified generation for {len(successful_files)} documents.",
                    },
                }
            except Exception as e:
                logger.error(f"Batch merge failed: {e}")

        return {
            "success": len(successful_files) > 0,
            "documents": results,
            "merged": merged,
            "total": len(template_names),
            "successful": len(successful_files),
        }

    def _packet_templates(self, packet_config: dict[str, Any]) -> list[str]:
        templates = list(packet_config.get("templates", []))
        if packet_config.get("include_cover_page"):
            cover_template = packet_config.get("cover_page_template", "All_Cover.pdf")
            if cover_template and cover_template not in templates:
                templates.insert(0, cover_template)
        return templates

    def generate_packet(
        self, packet_name: str, data: dict[str, Any], deal_id: str | None = None
    ) -> dict[str, Any]:
        """
        Generate a document packet (backward compatibility wrapper for generate_batch).
        """
        packet_config = self.schema.get("packets", {}).get(packet_name)
        if not packet_config:
            packet_config = self.legacy_schema.get("packets", {}).get(packet_name)

        if not packet_config:
            return {"success": False, "message": f"Packet {packet_name} not found"}

        templates = self._packet_templates(packet_config)
        return self.generate_batch(templates, data, merge=True, deal_id=deal_id)

    def list_available_templates(self) -> list[dict[str, Any]]:
        v2_templates = {k: v for k, v in self.schema.get("templates", {}).items()}
        legacy_templates = self.legacy_schema.get("templates", {})

        # Merge, prioritizing v2
        all_templates = {}
        for name, cfg in legacy_templates.items():
            all_templates[name] = {
                "template_name": name,
                "display_name": cfg.get("display_name", name),
                "category": cfg.get("category", "Legacy"),
                "description": cfg.get("description", ""),
                "is_v2": False,
            }

        for name, cfg in v2_templates.items():
            all_templates[name] = {
                "template_name": name,
                "display_name": cfg.get("display_name", name),
                "category": cfg.get("category", "Other"),
                "description": cfg.get("description", ""),
                "is_v2": True,
            }

        return list(all_templates.values())

    def list_available_packets(self) -> list[dict[str, Any]]:
        v2_packets = self.schema.get("packets", {})
        legacy_packets = self.legacy_schema.get("packets", {})

        all_packets = {}
        for name, cfg in legacy_packets.items():
            all_packets[name] = {
                "packet_name": name,
                "display_name": cfg.get("display_name", name),
                "description": cfg.get("description", ""),
                "template_count": len(self._packet_templates(cfg)),
                "templates": self._packet_templates(cfg),
            }

        for name, cfg in v2_packets.items():
            all_packets[name] = {
                "packet_name": name,
                "display_name": cfg.get("display_name", name),
                "description": cfg.get("description", ""),
                "template_count": len(self._packet_templates(cfg)),
                "templates": self._packet_templates(cfg),
            }

        return list(all_packets.values())

    def get_all_field_definitions(self) -> dict[str, Any]:
        return self.schema.get("domains", {})

    def get_template_fields(self, template_name: str) -> dict[str, Any] | None:
        tpl = self.schema["templates"].get(template_name)
        if not tpl:
            return None

        # This drives the frontend SmartForm. We need to group fields by domain.
        sections = {}
        for domain_name, domain_cfg in self.schema["domains"].items():
            sections[domain_name] = []
            for field_name, field_cfg in domain_cfg["fields"].items():
                sections[domain_name].append(
                    {
                        "field_name": field_name,
                        "label": field_cfg.get("label", field_name),
                        "type": field_cfg.get("type", "string"),
                        "required": field_cfg.get("required", False),
                    }
                )

        return {
            "template_name": template_name,
            "display_name": tpl.get("display_name", template_name),
            "category": tpl.get("category", "Other"),
            "sections": sections,
        }


# ─── Singleton Instance & Proxy Functions ──────────────────────────────────

_engine = DocumentEngineV2()


def generate_document(template_name, data, output_filename=None, deal_id=None):
    return _engine.generate_document(template_name, data, output_filename, deal_id)


def generate_batch(template_names, data, merge=True, deal_id=None):
    return _engine.generate_batch(template_names, data, merge, deal_id)


def generate_packet(packet_name, data, deal_id=None):
    return _engine.generate_packet(packet_name, data, deal_id)


def list_available_templates():
    return _engine.list_available_templates()


def list_available_packets():
    return _engine.list_available_packets()


def get_all_field_definitions():
    return _engine.get_all_field_definitions()


def get_template_fields(template_name):
    return _engine.get_template_fields(template_name)
