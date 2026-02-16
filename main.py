"""
FastAPI Application — Config-Driven AI Agent Server

Serves the AI agent backend and static frontend.
All business-specific config is loaded from config.yaml.
"""

import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.adk.runners import InMemoryRunner
from google.adk.apps import App
import google.genai
import vertexai
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from config_loader import get_deployment_config, business_name

# Initialize Vertex AI
deploy_cfg = get_deployment_config()
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", deploy_cfg.get("project_id", "tho-ai-agent"))
location = os.environ.get("GOOGLE_CLOUD_LOCATION", deploy_cfg.get("region", "us-central1"))
vertexai.init(project=project_id, location=location)

from root_agent import root_agent
from structured_logging import logger as struct_logger
from conversation_memory import ConversationMemory
from lead_management import LeadManager, Lead
from appointment_manager import AppointmentManager, Appointment

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=f"{business_name()} AI Agent")

# Initialize services
conversation_memory = ConversationMemory(project_id=project_id)
lead_manager = LeadManager(project_id=project_id)
appointment_manager = AppointmentManager(project_id=project_id)

# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Add CORS — production origins only; add localhost in dev
IS_LOCAL = os.environ.get("K_SERVICE") is None  # K_SERVICE is set by Cloud Run
ALLOWED_ORIGINS = [
    "https://tho-agent-691674245427.us-central1.run.app",
    "https://tho-agent-trgi34bxuq-uc.a.run.app",
    "https://tho-ai-agent.web.app",
    "https://tho-ai-agent.firebaseapp.com",
]
if IS_LOCAL:
    ALLOWED_ORIGINS += ["http://localhost:8080", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

# Initialize ADK Runner
adk_app = App(name="root_agent", root_agent=root_agent)
runner = InMemoryRunner(app=adk_app)


@app.post("/run")
async def run_agent(request: Request):
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    try:
        data = await request.json()
        user_id = data.get("userId", "default_user")
        session_id = data.get("sessionId", "default_session")
        new_message_dict = data.get("newMessage")
        
        # Extract text content
        text_content = ""
        if new_message_dict and "parts" in new_message_dict:
            text_content = new_message_dict["parts"][0].get("text", "")
        
        struct_logger.request(request_id, user_id, session_id, text_content)
        
        # Get conversation context
        context = None
        try:
            context = await conversation_memory.get_context(session_id, user_id)
            context_prompt = context.preferences.to_prompt_context()
            struct_logger.info("Context retrieved", request_id=request_id, has_preferences=bool(context_prompt))
        except Exception as e:
            struct_logger.warning("Context retrieval failed", request_id=request_id, error=str(e))

        # Create ADK Content object
        from google.genai import types
        new_message = types.Content(
            role="user",
            parts=[types.Part(text=text_content)]
        )

        # Ensure session exists
        existing_session = await runner.session_service.get_session(
            app_name="root_agent",
            user_id=user_id,
            session_id=session_id
        )
        
        if not existing_session:
            struct_logger.info("Session created", request_id=request_id, session_id=session_id)
            await runner.session_service.create_session(
                app_name="root_agent",
                user_id=user_id,
                session_id=session_id
            )

        # Run agent
        result_generator = runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message
        )
        
        final_text = ""
        event_count = 0
        
        async for event in result_generator:
            event_count += 1
            struct_logger.info(f"Event {event_count}", 
                request_id=request_id,
                event_type=type(event).__name__,
                has_content=hasattr(event, "content") and event.content is not None)
            
            if hasattr(event, "content") and event.content and event.content.role == "model":
                for i, part in enumerate(event.content.parts):
                    has_text = hasattr(part, "text") and bool(part.text)
                    if has_text:
                        final_text += part.text
        
        if not final_text:
            struct_logger.warning("No text generated", request_id=request_id)
            final_text = "I apologize, but I couldn't generate a response. Please try again."

        # Update conversation context
        try:
            context = await conversation_memory.update_from_interaction(
                session_id=session_id,
                user_id=user_id,
                user_message=text_content,
                search_results=None
            )
            struct_logger.info("Context updated", request_id=request_id)
            
            # Auto-create/update lead from conversation
            try:
                existing_lead = await lead_manager.get_lead_by_session(session_id)
                if existing_lead and context:
                    existing_lead.bedrooms = context.preferences.bedrooms or existing_lead.bedrooms
                    existing_lead.bathrooms = context.preferences.bathrooms or existing_lead.bathrooms
                    existing_lead.budget_max = context.preferences.max_budget or existing_lead.budget_max
                    existing_lead.homes_viewed = context.homes_discussed
                    existing_lead.appointment_requested = context.appointment_intent
                    existing_lead.financing_discussed = context.financing_questions > 0
                    await lead_manager.update_lead(existing_lead)
                elif context and (context.preferences.bedrooms or context.preferences.max_budget or context.homes_discussed):
                    new_lead = Lead(
                        lead_id=f"lead_{session_id[:8]}_{int(time.time())}",
                        user_id=user_id,
                        session_id=session_id,
                        bedrooms=context.preferences.bedrooms,
                        bathrooms=context.preferences.bathrooms,
                        budget_max=context.preferences.max_budget,
                        homes_viewed=context.homes_discussed,
                        appointment_requested=context.appointment_intent,
                        financing_discussed=context.financing_questions > 0,
                        source="chat"
                    )
                    await lead_manager.create_lead(new_lead)
            except Exception as e:
                struct_logger.warning("Lead management failed", request_id=request_id, error=str(e))
                
        except Exception as e:
            struct_logger.warning("Context update failed", request_id=request_id, error=str(e))

        duration_ms = (time.time() - start_time) * 1000
        struct_logger.response(request_id, len(final_text), duration_ms)
        
        return {"text": final_text}
        
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        struct_logger.error("Request failed", request_id=request_id, error=str(e), duration_ms=duration_ms)
        user_message = "Something went wrong. Please try again." if not IS_LOCAL else str(e)
        return {"error": user_message}


@app.get("/leads/export")
async def export_leads(status: str = None):
    """Export leads to CSV format."""
    try:
        leads = await lead_manager.list_leads(status=status, limit=1000)
        if not leads:
            return {"message": "No leads found", "count": 0}
        
        import io, csv
        from fastapi.responses import StreamingResponse
        
        output = io.StringIO()
        if leads:
            fieldnames = list(leads[0].to_csv_row().keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for lead in leads:
                writer.writerow(lead.to_csv_row())
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=leads_{status or 'all'}_{int(time.time())}.csv"}
        )
    except Exception as e:
        struct_logger.error("Lead export failed", error=str(e))
        return {"error": str(e)}, 500


@app.get("/leads/stats")
async def get_lead_stats():
    """Get lead statistics."""
    try:
        all_leads = await lead_manager.list_leads(limit=500)
        stats = {
            "total": len(all_leads),
            "by_status": {},
            "with_contact_info": 0,
            "appointment_requested": 0,
            "financing_discussed": 0
        }
        for lead in all_leads:
            stats["by_status"][lead.status] = stats["by_status"].get(lead.status, 0) + 1
            if lead.email or lead.phone:
                stats["with_contact_info"] += 1
            if lead.appointment_requested:
                stats["appointment_requested"] += 1
            if lead.financing_discussed:
                stats["financing_discussed"] += 1
        return stats
    except Exception as e:
        struct_logger.error("Lead stats failed", error=str(e))
        return {"error": "Failed to load lead statistics"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
async def create_session(app_name: str, user_id: str, session_id: str):
    logger.info(f"Creating session: {session_id} for user: {user_id}")
    try:
        await runner.session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        return {"status": "created", "session_id": session_id}
    except Exception as e:
        logger.error(f"Error creating session: {str(e)}")
        return {"status": "error", "message": str(e)}



# Document Generation Endpoints
from schemas.document_schemas import SalesContractForm, GenerateDocumentRequest, GeneratePacketRequest
from tools.document_tools import generate_sales_contract_pdf, OUTPUT_DIR
from tools.document_engine import (
    generate_document as engine_generate_document,
    generate_packet as engine_generate_packet,
    list_available_templates as engine_list_templates,
    list_available_packets as engine_list_packets,
    get_template_fields as engine_get_template_fields,
)

# --- Phase 2: Generic Document Engine Endpoints ---

@app.get("/api/documents/templates")
async def list_templates():
    """List all available document templates with metadata."""
    try:
        templates = engine_list_templates()
        packets = engine_list_packets()
        return {"templates": templates, "packets": packets}
    except Exception as e:
        struct_logger.error("Template listing failed", error=str(e))
        return {"error": str(e)}

@app.get("/api/documents/templates/{template_name}/fields")
async def get_template_fields(template_name: str):
    """Get field definitions for a specific template (drives SmartForm)."""
    try:
        fields = engine_get_template_fields(template_name)
        if fields is None:
            return {"error": f"Template '{template_name}' not found"}
        return fields
    except Exception as e:
        struct_logger.error("Template field lookup failed", error=str(e))
        return {"error": str(e)}

@app.post("/api/documents/generate")
async def generate_document_endpoint(request: GenerateDocumentRequest):
    """Generate any mapped document template."""
    try:
        result = engine_generate_document(
            template_name=request.template_name,
            data=request.data,
        )
        if result["success"]:
            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result["filename"],
                "message": result["message"],
            }
        return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Document generation failed", error=str(e))
        return {"success": False, "error": str(e)}

@app.post("/api/documents/generate-packet")
async def generate_packet_endpoint(request: GeneratePacketRequest):
    """Generate a closing packet (multiple merged PDFs)."""
    try:
        result = engine_generate_packet(
            packet_name=request.packet_name,
            data=request.data,
        )
        if result["success"]:
            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result["filename"],
                "message": result["message"],
                "page_count": result.get("page_count", 0),
                "documents_included": result.get("documents_included", []),
            }
        return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Packet generation failed", error=str(e))
        return {"success": False, "error": str(e)}

@app.post("/api/documents/extract-fields")
async def extract_fields_from_chat(request: Request):
    """Extract form field data from chat conversation history using AI."""
    try:
        data = await request.json()
        session_id = data.get("session_id")
        template_name = data.get("template_name")

        if not session_id or not template_name:
            return {"extracted_data": {}, "message": "session_id and template_name required"}

        from tools.form_extraction import extract_form_data_from_session
        result = await extract_form_data_from_session(
            session_id=session_id,
            template_name=template_name,
            runner=runner if 'runner' in dir() else None,
        )
        return result
    except Exception as e:
        struct_logger.error("Field extraction failed", error=str(e))
        return {"extracted_data": {}, "message": str(e)}

# --- Legacy Endpoint (Phase 1 backward compatibility) ---

@app.post("/api/documents/sales-contract")
async def create_sales_contract(form_data: SalesContractForm):
    """Generate a Sales Contract PDF (legacy endpoint — use /api/documents/generate instead)."""
    try:
        result = generate_sales_contract_pdf(form_data)
        if result["success"]:
            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result['filename']
            }
        else:
            return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Document generation failed", error=str(e))
        return {"success": False, "error": str(e)}

@app.get("/api/documents/download/{filename}")
async def download_document(filename: str):
    """Download a generated document."""
    # Security: sanitize filename to prevent path traversal attacks
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or ".." in filename:
        return {"error": "Invalid filename"}, 400
    file_path = os.path.join(OUTPUT_DIR, safe_filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=safe_filename, media_type='application/pdf')
    return {"error": "File not found"}, 404


# ─── Deals API (replaces fastcontractdocs.com) ───
from database.firestore_client import get_database
from database.models import Deal, DealStatus

_deal_db = get_database()


@app.get("/api/deals")
async def list_deals(status: str = None, salesrep: str = None, q: str = None, limit: int = 50):
    """List deals with optional filters."""
    try:
        deals = _deal_db.search_deals(
            status=status,
            salesrep=salesrep,
            buyer_name=q,
            limit=limit
        )
        return {"success": True, "deals": deals, "count": len(deals)}
    except Exception as e:
        struct_logger.error("Deal listing failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.post("/api/deals")
async def create_deal(request: Request):
    """Create a new deal/application."""
    try:
        data = await request.json()
        # Generate ID if not provided
        deal = Deal(**data)
        deal_data = deal.model_dump()
        # Convert datetime to string for Firestore
        for key in ["created_at", "updated_at"]:
            if key in deal_data and deal_data[key]:
                deal_data[key] = deal_data[key].isoformat() if hasattr(deal_data[key], 'isoformat') else str(deal_data[key])
        deal_id = _deal_db.create_deal(deal_data)
        # Return full deal object so frontend can navigate to detail view
        created_deal = _deal_db.get_deal(deal_id)
        return {"success": True, "deal_id": deal_id, "deal": created_deal, "message": "Deal created successfully"}
    except Exception as e:
        struct_logger.error("Deal creation failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.get("/api/deals/{deal_id}")
async def get_deal(deal_id: str):
    """Get deal details."""
    try:
        deal = _deal_db.get_deal(deal_id)
        if not deal:
            return {"success": False, "error": "Deal not found"}
        return {"success": True, "deal": deal}
    except Exception as e:
        struct_logger.error("Deal fetch failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.put("/api/deals/{deal_id}")
async def update_deal(deal_id: str, request: Request):
    """Update deal data."""
    try:
        data = await request.json()
        # Don't allow overwriting id or timestamps
        data.pop("id", None)
        data.pop("created_at", None)
        _deal_db.update_deal(deal_id, data)
        return {"success": True, "message": "Deal updated successfully"}
    except Exception as e:
        struct_logger.error("Deal update failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.put("/api/deals/{deal_id}/status")
async def update_deal_status(deal_id: str, request: Request):
    """Change deal status."""
    try:
        data = await request.json()
        new_status = data.get("status")
        valid_statuses = [s.value for s in DealStatus]
        if new_status not in valid_statuses:
            return {"success": False, "error": f"Invalid status. Must be one of: {valid_statuses}"}
        _deal_db.update_deal(deal_id, {"status": new_status})
        return {"success": True, "message": f"Deal status changed to {new_status}"}
    except Exception as e:
        struct_logger.error("Deal status update failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.post("/api/deals/{deal_id}/generate-document")
async def generate_document_from_deal(deal_id: str, request: Request):
    """Generate a document pre-filled with deal data."""
    try:
        data = await request.json()
        template_name = data.get("template_name")
        if not template_name:
            return {"success": False, "error": "template_name is required"}

        # Fetch deal and convert to document data
        deal_data = _deal_db.get_deal(deal_id)
        if not deal_data:
            return {"success": False, "error": "Deal not found"}

        deal = Deal(**{k: v for k, v in deal_data.items() if k != "id" or k == "id"})
        doc_data = deal.to_document_data()

        # Allow overrides from request body
        overrides = data.get("overrides", {})
        doc_data.update(overrides)

        result = engine_generate_document(
            template_name=template_name,
            data=doc_data,
        )
        if result["success"]:
            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result["filename"],
                "message": result["message"],
            }
        return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Deal document generation failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.post("/api/deals/{deal_id}/generate-packet")
async def generate_packet_from_deal(deal_id: str, request: Request):
    """Generate a closing packet pre-filled with deal data."""
    try:
        data = await request.json()
        packet_name = data.get("packet_name", "standard_closing")

        # Fetch deal and convert to document data
        deal_data = _deal_db.get_deal(deal_id)
        if not deal_data:
            return {"success": False, "error": "Deal not found"}

        deal = Deal(**{k: v for k, v in deal_data.items() if k != "id" or k == "id"})
        doc_data = deal.to_document_data()

        # Allow overrides
        overrides = data.get("overrides", {})
        doc_data.update(overrides)

        result = engine_generate_packet(
            packet_name=packet_name,
            data=doc_data,
        )
        if result["success"]:
            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result["filename"],
                "message": result["message"],
                "page_count": result.get("page_count", 0),
                "documents_included": result.get("documents_included", []),
            }
        return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Deal packet generation failed", error=str(e))
        return {"success": False, "error": str(e)}


# ─── Marketing API (Tex's Ad Studio) ───
from tools.marketing_tools import (
    generate_content_script,
    get_trending_content_ideas,
    schedule_social_post,
    analyze_content_performance,
    generate_ad_image,
    get_inventory_for_ads,
    GENERATED_ADS_DIR
)
from tools.asset_scraper import get_all_assets, PROPERTY_ASSETS, get_matterport_url

@app.post("/api/marketing/generate-script")
async def api_generate_script(request: Request):
    """Generate viral-ready video scripts for social media with optional A/B variations."""
    try:
        data = await request.json()
        result = generate_content_script(
            home_id=data.get("home_id"),
            home_name=data.get("home_name"),
            home_price=data.get("home_price"),
            home_specs=data.get("home_specs"),
            content_theme=data.get("content_theme", "home_tour"),
            platform=data.get("platform", "tiktok"),
            custom_hook=data.get("custom_hook"),
            language=data.get("language", "en"),
            avatar=data.get("avatar", "tex_classic"),
            custom_avatar_prompt=data.get("custom_avatar_prompt"),
            variations=data.get("variations", 1)
        )
        return result
    except Exception as e:
        struct_logger.error("Script generation failed", error=str(e))
        return {"error": str(e)}

@app.get("/api/marketing/trending-ideas")
async def api_trending_ideas():
    """Get AI-generated trending content ideas."""
    try:
        result = get_trending_content_ideas()
        return result
    except Exception as e:
        struct_logger.error("Trending ideas failed", error=str(e))
        return {"error": str(e)}

@app.post("/api/marketing/schedule")
async def api_schedule_post(request: Request):
    """Schedule a post for publishing."""
    try:
        data = await request.json()
        result = schedule_social_post(
            platform=data.get("platform", "tiktok"),
            content_type=data.get("content_type", "video"),
            script_id=data.get("script_id"),
            post_time=data.get("post_time"),
            caption=data.get("caption"),
            hashtags=data.get("hashtags"),
            video_url=data.get("video_url")
        )
        return result
    except Exception as e:
        struct_logger.error("Post scheduling failed", error=str(e))
        return {"error": str(e)}

@app.get("/api/marketing/analytics")
async def api_content_analytics():
    """Get content performance analytics."""
    try:
        result = analyze_content_performance()
        return result
    except Exception as e:
        struct_logger.error("Analytics load failed", error=str(e))
        return {"error": str(e)}


@app.post("/api/marketing/generate-image")
async def api_generate_image(request: Request):
    """Generate a marketing image using Google Imagen."""
    try:
        data = await request.json()
        prompt = data.get("prompt", "").strip()
        if not prompt:
            return {"success": False, "error": "A prompt is required to generate an image."}

        result = generate_ad_image(
            prompt=prompt,
            home_name=data.get("home_name"),
            platform=data.get("platform", "tiktok"),
            style=data.get("style", "photorealistic"),
            aspect_ratio=data.get("aspect_ratio"),
        )
        return result
    except Exception as e:
        struct_logger.error("Image generation failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.get("/api/marketing/inventory-context")
async def api_inventory_context():
    """Get inventory highlights for ad creation — combines Firestore inventory + website assets."""
    try:
        # Get Firestore inventory (FCD-imported homes)
        result = get_inventory_for_ads(limit=30)
        firestore_homes = result.get("homes", [])
        firestore_names = {h["model_name"].lower() for h in firestore_homes}

        # Get website homes from asset catalog (THO lot homes)
        website_homes = []
        for slug, asset in PROPERTY_ASSETS.items():
            # Skip if already matched from Firestore
            if asset["name"].lower() in firestore_names:
                continue
            home_data = {
                "id": slug,
                "model_name": asset["name"],
                "manufacturer": asset.get("manufacturer", "New Vision Manufacturing"),
                "classification": "Manufactured Home",
                "status": "Available" if asset.get("is_new") else "Pre-Owned",
                "display_price": "Call for Price",
                "price_value": 0,
                "specs": {
                    "beds": asset.get("beds"),
                    "baths": asset.get("baths"),
                    "sq_ft": asset.get("sqft"),
                    "dimensions": asset.get("dims"),
                },
                "features": [],
                "image_url": asset.get("floor_plan", ""),
                "gallery_images": asset.get("images", [])[:3],
                "real_photos": asset.get("images", []),
                "image_categories": asset.get("image_categories", {}),
                "floor_plan_url": asset.get("floor_plan"),
                "matterport_id": asset.get("matterport_id"),
                "matterport_url": get_matterport_url(asset["matterport_id"]) if asset.get("matterport_id") else None,
            }
            website_homes.append(home_data)

        # Merge: website homes (with photos) first, then Firestore homes
        all_homes = website_homes + firestore_homes
        result["homes"] = all_homes
        result["total_inventory"] = len(all_homes)
        result["website_homes"] = len(website_homes)
        return result
    except Exception as e:
        struct_logger.error("Inventory context failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.get("/api/marketing/images/{filename}")
async def download_ad_image(filename: str):
    """Download a generated ad image."""
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or ".." in filename:
        return {"error": "Invalid filename"}, 400
    file_path = os.path.join(GENERATED_ADS_DIR, safe_filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=safe_filename, media_type='image/png')
    return {"error": "Image not found"}, 404


# ─── Contact Form API ───

@app.post("/api/contact")
async def submit_contact_form(request: Request):
    """Receive contact form submissions and log as leads."""
    try:
        data = await request.json()
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        message = data.get("message", "").strip()

        if not name or not phone:
            return {"success": False, "error": "Name and phone are required"}

        struct_logger.info("Contact form submitted", name=name, has_phone=bool(phone))

        # Create a lead from the contact form
        try:
            new_lead = Lead(
                lead_id=f"contact_{int(time.time())}_{uuid.uuid4().hex[:4]}",
                user_id="contact_form",
                session_id=f"contact_{int(time.time())}",
                source="contact_form",
                name=name,
                phone=phone,
            )
            await lead_manager.create_lead(new_lead)
        except Exception as e:
            struct_logger.warning("Contact lead creation failed", error=str(e))

        return {"success": True, "message": "Thank you! We'll be in touch shortly."}
    except Exception as e:
        struct_logger.error("Contact form failed", error=str(e))
        return {"success": False, "error": str(e)}


# ─── Appointment Scheduling API ───

@app.get("/api/appointments/slots")
async def get_available_slots(date: str):
    """Get available appointment slots for a given date."""
    try:
        result = await appointment_manager.get_available_slots(date)
        return result
    except Exception as e:
        struct_logger.error("Slots lookup failed", error=str(e))
        return {"error": str(e)}


@app.post("/api/appointments")
async def create_appointment(request: Request):
    """Book a new appointment."""
    try:
        data = await request.json()
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        appt_date = data.get("date", "").strip()
        time_slot = data.get("time_slot", "").strip()

        if not name or not phone or not appt_date or not time_slot:
            return {"success": False, "error": "Name, phone, date, and time_slot are required."}

        import re
        phone_digits = re.sub(r'\D', '', phone)
        if len(phone_digits) < 10:
            return {"success": False, "error": "Please provide a valid 10-digit phone number."}

        appt = Appointment(
            appointment_id=f"appt_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            name=name,
            phone=phone,
            email=data.get("email", "").strip() or None,
            date=appt_date,
            time_slot=time_slot,
            notes=data.get("notes", "").strip() or None,
            source=data.get("source", "website"),
        )

        created = await appointment_manager.create_appointment(appt)

        # Also create a lead for the CRM funnel
        try:
            lead = Lead(
                lead_id=f"appt_lead_{int(time.time())}_{uuid.uuid4().hex[:4]}",
                user_id="appointment",
                session_id=f"appt_{int(time.time())}",
                source="appointment",
                name=name,
                phone=phone,
                email=appt.email,
                appointment_requested=True,
            )
            await lead_manager.create_lead(lead)
        except Exception as e:
            struct_logger.warning("Appointment lead creation failed", error=str(e))

        return {
            "success": True,
            "appointment": created.to_dict(),
            "message": f"Appointment confirmed for {appt_date} at {time_slot}. We look forward to seeing you!"
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        struct_logger.error("Appointment creation failed", error=str(e))
        return {"success": False, "error": str(e)}


@app.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str):
    """Get appointment details by ID."""
    try:
        appt = await appointment_manager.get_appointment(appointment_id)
        if not appt:
            return {"error": "Appointment not found."}
        return appt.to_dict()
    except Exception as e:
        struct_logger.error("Appointment lookup failed", error=str(e))
        return {"error": str(e)}


@app.post("/api/appointments/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: str, request: Request):
    """Cancel an appointment."""
    try:
        data = await request.json()
        phone = data.get("phone", "").strip()

        appt = await appointment_manager.get_appointment(appointment_id)
        if not appt:
            return {"success": False, "error": "Appointment not found."}

        # Simple verification: phone must match
        if phone:
            import re
            appt_digits = re.sub(r'\D', '', appt.phone)
            req_digits = re.sub(r'\D', '', phone)
            if appt_digits != req_digits:
                return {"success": False, "error": "Phone number does not match this appointment."}

        cancelled = await appointment_manager.cancel_appointment(appointment_id)
        return {
            "success": True,
            "message": f"Appointment on {cancelled.date} at {cancelled.time_slot} has been cancelled."
        }
    except Exception as e:
        struct_logger.error("Appointment cancellation failed", error=str(e))
        return {"success": False, "error": str(e)}


# Serve Frontend — Must be last to avoid catching API routes
app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Serve actual files from dist if they exist (e.g., tex-icon.svg, vite.svg)
    if full_path:
        file_path = os.path.join("frontend/dist", full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
    return FileResponse("frontend/dist/index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
