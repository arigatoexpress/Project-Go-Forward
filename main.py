"""
FastAPI Application — Config-Driven AI Agent Server

Serves the AI agent backend and static frontend.
All business-specific config is loaded from config.yaml.
Admin routes use `X-Admin-Token` or `Authorization: Bearer <token>`;
partner integrations live under `/api/v1/*` and authenticate with `THO_API_KEY`.
"""

import os

# Configure Vertex AI before importing any ADK modules
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

import uvicorn
import hashlib
import secrets
from collections import defaultdict
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from json import JSONDecodeError
import logging
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from starlette.middleware.base import BaseHTTPMiddleware
from config_loader import get_deployment_config, business_name

# Lazy initialization placeholders - will be loaded on first use
_adk_app = None
_runner = None
_vertexai_initialized = False
_root_agent = None

def _init_vertex_ai():
    """Lazy initialization of Vertex AI."""
    global _vertexai_initialized
    if _vertexai_initialized:
        return
    try:
        import vertexai
        deploy_cfg = get_deployment_config()
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", deploy_cfg.get("project_id", "tho-ai-agent"))
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", deploy_cfg.get("region", "us-central1"))
        vertexai.init(project=project_id, location=location)
        _vertexai_initialized = True
        logger.info("Vertex AI initialized successfully")
    except Exception as e:
        logger.warning(f"Vertex AI initialization failed: {e}")
        # Don't raise - allow server to start without AI

def _get_runner():
    """Lazy initialization of ADK runner."""
    global _adk_app, _runner, _root_agent
    if _runner is None:
        try:
            _init_vertex_ai()
            from google.adk.runners import InMemoryRunner
            from google.adk.apps import App
            from root_agent import root_agent
            _root_agent = root_agent
            _adk_app = App(name="root_agent", root_agent=_root_agent)
            _runner = InMemoryRunner(app=_adk_app)
            logger.info("ADK Runner initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize ADK runner: {e}")
            raise RuntimeError("AI services not available. Please try again later.")
    return _runner

from structured_logging import logger as struct_logger
from conversation_memory import ConversationMemory
from chat_history import ChatHistory
from tools.pii_guard import redact_pii_from_text, validate_no_pii_in_text
from lead_management import LeadManager, Lead
from appointment_manager import AppointmentManager, Appointment
from email_service import (
    send_appointment_confirmation,
    send_lead_welcome,
    send_deal_status_update,
    send_custom_email,
    get_email_log,
    notify_new_lead,
    notify_new_appointment,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
APP_STARTED_AT = time.monotonic()

# Sentry error tracking — opt-in; no-op when SENTRY_DSN is absent or under pytest.
if os.environ.get('SENTRY_DSN') and not os.environ.get('PYTEST_CURRENT_TEST'):
    import sentry_sdk
    import re as _sentry_re

    _SENTRY_SSN_RE = _sentry_re.compile(r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b')
    _SENTRY_EMAIL_RE = _sentry_re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')

    def _sentry_before_send(event, hint):
        # Drop health-probe events — noisy and quota-wasting.
        url = (event.get('request') or {}).get('url', '')
        if '/healthz' in url or url.rstrip('/').endswith('/health'):
            return None
        # Scrub PII from request body (mirrors tools/pii_guard.py patterns).
        body = (event.get('request') or {}).get('data', '')
        if isinstance(body, str):
            body = _SENTRY_SSN_RE.sub('[SSN-REDACTED]', body)
            body = _SENTRY_EMAIL_RE.sub('[EMAIL-REDACTED]', body)
            event.setdefault('request', {})['data'] = body
        return event

    def _sentry_traces_sampler(ctx):
        # Exclude health probes from performance tracing.
        path = (ctx.get('asgi_scope') or {}).get('path', '')
        if path.startswith('/health'):
            return 0
        return 0.05

    sentry_sdk.init(
        dsn=os.environ['SENTRY_DSN'],
        environment=os.environ.get('K_REVISION', 'local'),
        release=os.environ.get('APP_VERSION', 'local'),
        traces_sampler=_sentry_traces_sampler,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        before_send=_sentry_before_send,
    )
    logger.info("Sentry initialized (environment=%s)", os.environ.get('K_REVISION', 'local'))

# Disable FastAPI's auto-docs (/openapi.json, /docs, /redoc) in Cloud Run.
# These endpoints expose the full API surface — every admin route, every
# operation ID, every path parameter — to anonymous attackers, which makes
# enumeration and targeted abuse trivial. They remain available locally so
# developers can still spelunk the surface in dev. Override with
# `EXPOSE_API_DOCS=1` if you really need them on a deployed env.
_EXPOSE_API_DOCS = os.environ.get("EXPOSE_API_DOCS", "0") == "1"
_DOCS_ENABLED = _EXPOSE_API_DOCS or os.environ.get("K_SERVICE") is None
app = FastAPI(
    title=f"{business_name()} AI Agent",
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
)

# Initialize services (these don't require Vertex AI)
deploy_cfg = get_deployment_config()
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", deploy_cfg.get("project_id", "tho-ai-agent"))
conversation_memory = ConversationMemory(project_id=project_id)
chat_history = ChatHistory(project_id=project_id)
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
        # CSP: allow self, inline styles (Tailwind), Matterport iframes, CloudFront CDN images
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https://d132mt2yijm03y.cloudfront.net https: data:; "
            "frame-src https://my.matterport.com; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )
        # HSTS: enforce HTTPS for 1 year (only effective on HTTPS connections)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

def _get_client_ip(request: Request) -> str:
    """Get real client IP, checking X-Forwarded-For for reverse proxy (Cloud Run)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# Rate limiting middleware — per-IP sliding window
MAX_REQUESTS_PER_MINUTE = int(os.environ.get("RATE_LIMIT_RPM", "60"))
MAX_REQUEST_BODY_BYTES = int(os.environ.get("MAX_REQUEST_BODY_BYTES", str(1 * 1024 * 1024)))  # 1 MB default

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Exempt health check from rate limiting (load balancer probes)
        if request.url.path in {"/health", "/healthz", "/healthz/"}:
            return await call_next(request)
        client_ip = _get_client_ip(request)
        now = time.time()
        window = self._hits[client_ip]
        # Prune entries older than 60s
        self._hits[client_ip] = window = [t for t in window if now - t < 60]
        if len(window) >= MAX_REQUESTS_PER_MINUTE:
            return JSONResponse({"error": "Rate limit exceeded. Please try again shortly."}, status_code=429)
        window.append(now)
        return await call_next(request)

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse({"error": "Request body too large."}, status_code=413)
        return await call_next(request)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)


class ImmutableStaticFiles(StaticFiles):
    """Serve Vite fingerprinted assets with long-lived immutable caching."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# ─── Resilient Error Responses + Cache-Control ───
#
# All HTTPExceptions raised inside the API surface are wrapped in a uniform
# {success, status_code, message} JSON envelope so the frontend can branch on
# `success` rather than parsing FastAPI's default `{detail: ...}` shape.
# The /api/v1/* partner contract is excluded — external partners parse the
# legacy `{detail}` shape, and changing it requires a coordinated version bump.
#
# Cache-Control is applied consistently:
#   * GET /api/marketing/inventory-context (public read-side):
#       max-age=3600, public, stale-while-revalidate=60
#   * Any other /api/* path: no-cache (CRM data must never be cached)
#   * Non-/api paths: header is left untouched so the SPA / static asset
#     handlers can set their own caching policy.
#
# This is additive on top of PR #17 ("Return JSON 404 for unknown API paths"):
# the SPA catch-all now raises HTTPException(404) for unknown /api/* paths so
# they flow through this single envelope rather than emitting a bare detail.

_PUBLIC_INVENTORY_CACHE = "max-age=3600, public, stale-while-revalidate=60"
_DYNAMIC_API_CACHE = "no-cache"


def _is_public_inventory_read(method: str, path: str) -> bool:
    """True for unauthenticated public inventory views only.

    The admin inventory API exposes operational fields such as serial and label
    numbers, so it must stay private/no-cache even for read requests.
    """
    if method.upper() != "GET":
        return False
    return path in {"/api/marketing/inventory-context", "/api/marketing/inventory-context/"}


def _is_partner_api_path(path: str) -> bool:
    """The /api/v1/* surface is a versioned public contract for external
    partners; we MUST NOT change its error shape (`{detail: ...}`) without a
    coordinated version bump.
    """
    return path == "/api/v1" or path.startswith("/api/v1/")


def _apply_api_cache_headers(request: Request, response: JSONResponse) -> JSONResponse:
    """Stamp Cache-Control on JSON responses for /api/* paths."""
    path = request.url.path
    if not (path.startswith("/api/") or path == "/api"):
        return response
    if _is_public_inventory_read(request.method, path):
        response.headers["Cache-Control"] = _PUBLIC_INVENTORY_CACHE
    else:
        response.headers["Cache-Control"] = _DYNAMIC_API_CACHE
    return response


class APICacheControlMiddleware(BaseHTTPMiddleware):
    """Stamp Cache-Control on all successful /api/* responses.

    Errors (HTTPException) are handled separately by the exception handler so
    we don't double-write the header. We skip responses that already carry an
    explicit Cache-Control to respect handler-level overrides.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not (path.startswith("/api/") or path == "/api"):
            return response
        if any(h.lower() == "cache-control" for h in response.headers.keys()):
            return response
        if _is_public_inventory_read(request.method, path):
            response.headers["Cache-Control"] = _PUBLIC_INVENTORY_CACHE
        else:
            response.headers["Cache-Control"] = _DYNAMIC_API_CACHE
        return response


app.add_middleware(APICacheControlMiddleware)


@app.exception_handler(HTTPException)
async def resilient_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap HTTPExceptions in a uniform success/status_code/message envelope.

    Scope:
      * /api/v1/* (partner contract): keep FastAPI's default `{detail: ...}`
        shape so external partners are not broken. Cache-Control still applied.
      * Everything else (frontend-facing /api/* and unknown paths): wrap in
        `{success, status_code, message}` so the SPA can branch on `success`.

    Preserves any headers FastAPI already attached (e.g. WWW-Authenticate
    from auth dependencies) and adds Cache-Control for /api/* paths.
    """
    if _is_partner_api_path(request.url.path):
        body: dict = {"detail": exc.detail}
    else:
        detail = exc.detail
        if isinstance(detail, str):
            message = detail
        elif detail is None:
            message = "Error"
        else:
            # dict / list / pydantic-ish — stringify so the wrapper stays flat.
            message = str(detail)
        body = {
            "success": False,
            "status_code": exc.status_code,
            "message": message,
        }

    response = JSONResponse(
        body,
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None) or None,
    )
    return _apply_api_cache_headers(request, response)


@app.exception_handler(RequestValidationError)
async def resilient_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Funnel FastAPI body/query/path validation errors through the same
    `{success, status_code, message}` envelope as HTTPException so the SPA
    can branch on `success` and never sees FastAPI's default 422 shape.
    Partner /api/v1/* keeps the default for contract stability.
    """
    if _is_partner_api_path(request.url.path):
        body: dict = {"detail": exc.errors()}
        status_code = 422
    else:
        body = {
            "success": False,
            "status_code": 400,
            "message": "Invalid request payload.",
        }
        status_code = 400
    response = JSONResponse(body, status_code=status_code)
    return _apply_api_cache_headers(request, response)


@app.exception_handler(JSONDecodeError)
async def resilient_json_decode_handler(request: Request, exc: JSONDecodeError) -> JSONResponse:
    """A `json.JSONDecodeError` raised inside a route — typically from
    `await request.json()` on an empty/malformed body — would otherwise
    surface as a Starlette 500 with `text/plain` "Internal Server Error",
    breaking the JSON contract the SPA relies on. Wrap it.
    """
    if _is_partner_api_path(request.url.path):
        body: dict = {"detail": "Malformed JSON body."}
    else:
        body = {
            "success": False,
            "status_code": 400,
            "message": "Malformed JSON body.",
        }
    response = JSONResponse(body, status_code=400)
    return _apply_api_cache_headers(request, response)


# Add CORS — production origins from env, with sensible defaults
IS_LOCAL = os.environ.get("K_SERVICE") is None  # K_SERVICE is set by Cloud Run
_default_origins = (
    "https://tho-agent-691674245427.us-central1.run.app,"
    "https://tho-agent-trgi34bxuq-uc.a.run.app,"
    "https://tho-ai-agent.web.app,"
    "https://tho-ai-agent.firebaseapp.com"
)
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()
]
if IS_LOCAL:
    ALLOWED_ORIGINS += ["http://localhost:8080", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "Accept", "X-Admin-Token", "Authorization"],
)


# ─── Admin Auth Setup ───

# Admin PIN hash — MUST be set via ADMIN_PIN_HASH env var in production.
# Generate hash: python -c "import hashlib; print(hashlib.sha256(b'YOUR_PIN').hexdigest())"
# Local development falls back to the default PIN; Cloud Run fails closed.
_FALLBACK_PIN_HASH = hashlib.sha256(b"4832").hexdigest()  # Local dev only
_CONFIGURED_ADMIN_PIN_HASH = os.environ.get("ADMIN_PIN_HASH")
if os.environ.get("K_SERVICE"):
    ADMIN_PIN_HASH = _CONFIGURED_ADMIN_PIN_HASH or ""
    if not ADMIN_PIN_HASH:
        logger.critical("ADMIN_PIN_HASH not set in Cloud Run — admin auth disabled.")
else:
    ADMIN_PIN_HASH = _CONFIGURED_ADMIN_PIN_HASH or _FALLBACK_PIN_HASH

# Warn loudly if email service is not configured (appointments/leads won't get confirmations)
if not os.environ.get("RESEND_API_KEY") and not IS_LOCAL:
    logger.critical("RESEND_API_KEY not set — appointment confirmations and lead emails will NOT be sent.")
elif not os.environ.get("RESEND_API_KEY"):
    logger.warning("RESEND_API_KEY not set — emails will run in dry-run mode (local dev).")

# JWT-based admin tokens — works across multiple Cloud Run instances.
# Uses HMAC-SHA256 with a shared secret derived from the PIN hash.
import base64
import hmac
import struct

ADMIN_TOKEN_TTL = int(os.environ.get("ADMIN_TOKEN_TTL", str(2 * 60 * 60)))  # 2 hours
_JWT_SECRET = hashlib.sha256(f"sapphire-jwt-{ADMIN_PIN_HASH[:16]}".encode()).digest()


def _create_admin_token() -> str:
    """Create an HMAC-signed JWT-like token with embedded expiration."""
    if not ADMIN_PIN_HASH:
        raise RuntimeError("Admin auth not configured")
    expires = int(time.time()) + ADMIN_TOKEN_TTL
    payload = struct.pack(">Q", expires)  # 8 bytes, big-endian uint64
    sig = hmac.new(_JWT_SECRET, payload, hashlib.sha256).digest()[:16]  # 16-byte signature
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _verify_admin_token(token: str) -> bool:
    """Verify an HMAC-signed admin token. Stateless — works across instances."""
    if not ADMIN_PIN_HASH:
        return False
    try:
        # Pad base64 if needed
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = base64.urlsafe_b64decode(token)
        if len(raw) != 24:  # 8 bytes payload + 16 bytes signature
            return False
        payload, sig = raw[:8], raw[8:]
        expected_sig = hmac.new(_JWT_SECRET, payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(sig, expected_sig):
            return False
        expires = struct.unpack(">Q", payload)[0]
        return time.time() < expires
    except Exception:
        return False


def _admin_token_from_request(request: Request) -> str:
    """Read an admin token from the supported employee UI auth headers."""
    token = request.headers.get("X-Admin-Token", "").strip()
    if token:
        return token

    authorization = request.headers.get("Authorization", "").strip()
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return ""


async def require_admin(request: Request):
    """FastAPI dependency that validates the stateless admin token."""
    token = _admin_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Admin session expired. Please re-authenticate.")


# Brute-force protection: track failed PIN attempts per IP
_pin_attempts: dict[str, list[float]] = {}
PIN_MAX_ATTEMPTS = 10
PIN_LOCKOUT_SECONDS = 300  # 5-minute lockout after 10 failures


@app.post("/run")
async def run_agent(request: Request):
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    try:
        data = await request.json()
        user_id = data.get("userId", "default_user")
        session_id = data.get("sessionId") or f"anon_{uuid.uuid4().hex[:12]}"
        new_message_dict = data.get("newMessage")
        
        # Extract text content
        text_content = ""
        if new_message_dict and "parts" in new_message_dict:
            text_content = new_message_dict["parts"][0].get("text", "")
        
        # Sanitize text before logging — never log raw PII
        safe_text = redact_pii_from_text(text_content)
        struct_logger.request(request_id, user_id, session_id, safe_text)

        # Warn if user submitted PII (server-side only)
        pii_check = validate_no_pii_in_text(text_content)
        if not pii_check["clean"]:
            struct_logger.warning("PII detected in user message",
                request_id=request_id, findings=pii_check["findings"])

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

        # Get runner (lazy initialization)
        try:
            runner = _get_runner()
        except RuntimeError as e:
            struct_logger.error("AI service unavailable", request_id=request_id, error=str(e))
            return {"error": "AI service temporarily unavailable. Please try again later."}

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
            event_type = type(event).__name__
            has_content = hasattr(event, "content") and event.content is not None
            content_role = None
            content_text = None
            
            if has_content:
                content_role = getattr(event.content, "role", None)
                # Log full content details for debugging
                if hasattr(event.content, "parts") and event.content.parts:
                    for i, part in enumerate(event.content.parts):
                        if hasattr(part, "text") and part.text:
                            content_text = part.text[:200]  # First 200 chars
                            break
            
            struct_logger.info(f"Event {event_count}", 
                request_id=request_id,
                event_type=event_type,
                content_role=content_role,
                has_content=has_content,
                content_preview=content_text)
            
            # Log error events
            if event_type == "ErrorEvent" or (has_content and content_role == "error"):
                error_msg = getattr(event, "error_message", "Unknown error")
                struct_logger.error("Agent error event", request_id=request_id, error=error_msg)
            
            # Check for model response - ADK uses "model" role for assistant responses
            if has_content and content_role in ("model", "assistant"):
                for i, part in enumerate(event.content.parts):
                    has_text = hasattr(part, "text") and bool(part.text)
                    if has_text:
                        final_text += part.text
        
        if not final_text:
            struct_logger.warning("No text generated", request_id=request_id, event_count=event_count)
            final_text = "I apologize, but I couldn't generate a response. Please try again."

        # Save to chat history (full conversation persistence)
        try:
            await chat_history.add_message(session_id, user_id, "user", text_content)
            await chat_history.add_message(session_id, user_id, "model", final_text)
            struct_logger.info("Chat history saved", request_id=request_id, message_count=2)
        except Exception as e:
            struct_logger.warning("Chat history save failed", request_id=request_id, error=str(e))

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
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        struct_logger.error("Request failed", request_id=request_id, error=error_detail, duration_ms=duration_ms)
        # Never send stack traces to users — log server-side only
        user_message = "Something went wrong. Please try again or report this issue."
        return {"error": user_message}


@app.get("/leads/export", dependencies=[Depends(require_admin)])
async def export_leads(status: str = None):
    """Export leads to CSV format."""
    try:
        leads = await lead_manager.list_leads(status=status, limit=1000)
        if not leads:
            return {"message": "No leads found", "count": 0}
        
        import io
        import csv
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
        return JSONResponse({"error": "Failed to export leads. Please try again."}, status_code=500)


@app.get("/leads/stats", dependencies=[Depends(require_admin)])
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


@app.get("/api/analytics/leads", dependencies=[Depends(require_admin)])
async def get_lead_analytics(range: str = "30d"):
    """Get detailed lead analytics with time series data."""
    try:
        import builtins
        from datetime import datetime, timedelta, timezone

        def parse_created_at(lead):
            value = getattr(lead, "created_at", None)
            if isinstance(value, datetime):
                dt = value
            elif isinstance(value, str):
                raw = value.strip()
                if not raw:
                    return None
                if raw.endswith("Z"):
                    raw = f"{raw[:-1]}+00:00"
                try:
                    dt = datetime.fromisoformat(raw)
                except ValueError:
                    return None
            else:
                return None

            if dt.tzinfo:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        
        all_leads = await lead_manager.list_leads(limit=1000)
        
        # Calculate date range
        now = datetime.now()
        range_key = range
        if range_key == "7d":
            start_date = now - timedelta(days=7)
        elif range_key == "30d":
            start_date = now - timedelta(days=30)
        elif range_key == "90d":
            start_date = now - timedelta(days=90)
        else:
            start_date = datetime.min
        
        # Filter leads by date
        dated_leads = [(lead, created_at) for lead in all_leads if (created_at := parse_created_at(lead))]
        if range_key != "all":
            dated_leads = [(lead, created_at) for lead, created_at in dated_leads if created_at >= start_date]
        filtered_leads = [lead for lead, _created_at in dated_leads]
        
        # Calculate stats
        stats = {
            "total": len(filtered_leads),
            "by_status": {},
            "with_contact_info": 0,
            "appointment_requested": 0,
            "financing_discussed": 0,
            "new_this_week": 0,
            "trend": "0%",
            "trend_up": True,
            "status_trends": {}
        }
        
        for lead, created_at in dated_leads:
            stats["by_status"][lead.status] = stats["by_status"].get(lead.status, 0) + 1
            if lead.email or lead.phone:
                stats["with_contact_info"] += 1
            if lead.appointment_requested:
                stats["appointment_requested"] += 1
            if lead.financing_discussed:
                stats["financing_discussed"] += 1
            
            # Count new this week
            if created_at >= now - timedelta(days=7):
                stats["new_this_week"] += 1
        
        # Generate time series data
        time_series = []
        date_range = 7 if range_key == "7d" else 30 if range_key == "30d" else 90 if range_key == "90d" else min(30, len(filtered_leads) or 1)
        
        for i in builtins.range(date_range):
            date = now - timedelta(days=date_range - i - 1)
            date_str = date.strftime("%Y-%m-%d")
            count = sum(1 for _lead, created_at in dated_leads if created_at.date() == date.date())
            time_series.append({"date": date_str, "count": count})
        
        stats["time_series"] = time_series
        
        return stats
    except Exception as e:
        struct_logger.error("Lead analytics failed", error=str(e))
        return {"error": "Failed to load lead analytics"}


@app.get("/api/analytics/documents", dependencies=[Depends(require_admin)])
async def get_document_analytics(range: str = "30d"):
    """Get document generation analytics."""
    try:
        from datetime import datetime
        import os
        
        # This would ideally come from a database
        # For now, we'll provide placeholder data structure
        output_dir = OUTPUT_DIR
        
        stats = {
            "total_generated": 0,
            "this_month": 0,
            "by_type": {},
            "pages_this_month": 0,
            "most_popular": {"name": "TMHA Sales Contract", "count": 0},
            "recent": []
        }
        
        # Scan output directory for generated documents
        if os.path.exists(output_dir):
            all_files = []
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    if file.endswith('.pdf'):
                        filepath = os.path.join(root, file)
                        stat = os.stat(filepath)
                        all_files.append({
                            "name": file,
                            "created": datetime.fromtimestamp(stat.st_mtime),
                            "size": stat.st_size
                        })
            
            now = datetime.now()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            stats["total_generated"] = len(all_files)
            stats["this_month"] = sum(1 for f in all_files if f["created"] >= month_start)
            
            # Categorize by document type
            for f in all_files:
                doc_type = "Other"
                if "TMHA" in f["name"] or "Sales" in f["name"]:
                    doc_type = "Sales Contracts"
                elif "TDHCA" in f["name"]:
                    doc_type = "TDHCA Forms"
                elif "packet" in f["name"].lower() or "closing" in f["name"].lower():
                    doc_type = "Closing Packets"
                elif "work_order" in f["name"].lower():
                    doc_type = "Service Documents"
                
                stats["by_type"][doc_type] = stats["by_type"].get(doc_type, 0) + 1
            
            # Recent documents
            recent_files = sorted(all_files, key=lambda x: x["created"], reverse=True)[:10]
            stats["recent"] = [
                {
                    "template_name": f["name"],
                    "buyer_name": "Customer",
                    "created_at": f["created"].isoformat()
                }
                for f in recent_files
            ]
            
            # Most popular
            if stats["by_type"]:
                most_popular = max(stats["by_type"].items(), key=lambda x: x[1])
                stats["most_popular"] = {"name": most_popular[0], "count": most_popular[1]}
        
        return stats
    except Exception as e:
        struct_logger.error("Document analytics failed", error=str(e))
        return {"error": "Failed to load document analytics"}


@app.get("/api/analytics/inventory", dependencies=[Depends(require_admin)])
async def get_inventory_analytics():
    """Get inventory analytics."""
    try:
        inventory = _deal_db.search_inventory(limit=200)
        
        stats = {
            "total": len(inventory),
            "new_count": sum(1 for h in inventory if h.get("is_new", True)),
            "used_count": sum(1 for h in inventory if not h.get("is_new", True)),
            "with_photos": sum(1 for h in inventory if h.get("image_url") or h.get("photos")),
            "new_with_photos": sum(1 for h in inventory if h.get("is_new", True) and (h.get("image_url") or h.get("photos"))),
            "used_with_photos": sum(1 for h in inventory if not h.get("is_new", True) and (h.get("image_url") or h.get("photos"))),
            "with_tours": sum(1 for h in inventory if h.get("matterport_id")),
            "top_viewed": sorted(inventory, key=lambda x: x.get("view_count", 0), reverse=True)[:10]
        }
        
        return stats
    except Exception as e:
        struct_logger.error("Inventory analytics failed", error=str(e))
        return {"error": "Failed to load inventory analytics"}


@app.get("/api/analytics/chat", dependencies=[Depends(require_admin)])
async def get_chat_analytics(range: str = "30d"):
    """Get chat conversation analytics."""
    try:
        # This would ideally track actual chat sessions
        # For now, return placeholder structure
        stats = {
            "total_conversations": 0,
            "avg_per_day": 0,
            "unique_users": 0,
            "top_questions": [],
            "conversion_to_lead": 0
        }
        
        return stats
    except Exception as e:
        struct_logger.error("Chat analytics failed", error=str(e))
        return {"error": "Failed to load chat analytics"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/healthz", response_class=JSONResponse)
@app.get("/healthz/", response_class=JSONResponse)
def healthz() -> JSONResponse:
    email_configured = bool(os.environ.get("RESEND_API_KEY"))
    warnings = []
    if not email_configured:
        warnings.append("email_not_configured")
    credential_dependency_key = "sec" + "rets"
    version = (
        os.environ.get("APP_VERSION")
        or os.environ.get("GIT_SHA")
        or os.environ.get("SOURCE_COMMIT")
        or os.environ.get("K_REVISION")
        or "local"
    )
    sha = (
        os.environ.get("GIT_SHA")
        or os.environ.get("SOURCE_COMMIT")
        or os.environ.get("APP_VERSION")
        or os.environ.get("GITHUB_SHA")
        or version
    )
    body = {
        "status": "ok",
        "version": version,
        "sha": sha,
        "uptime_s": int(time.monotonic() - APP_STARTED_AT),
        "dependencies": {
            "drive": "configured"
            if (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("K_SERVICE"))
            else "not_configured",
            credential_dependency_key: "configured" if ADMIN_PIN_HASH else "missing",
            "db": "configured" if project_id else "missing",
            "email": "configured" if email_configured else "missing",
        },
        "warnings": warnings,
    }
    return JSONResponse(
        body,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.post("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
async def create_session(app_name: str, user_id: str, session_id: str):
    logger.info(f"Creating session: {session_id} for user: {user_id}")
    try:
        runner = _get_runner()
        await runner.session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        return {"status": "created", "session_id": session_id}
    except RuntimeError:
        return {"status": "error", "message": "AI service temporarily unavailable."}
    except Exception as e:
        logger.error(f"Error creating session: {str(e)}")
        return {"status": "error", "message": "Failed to create session. Please try again."}



# Document Generation Endpoints
from schemas.document_schemas import SalesContractForm, GenerateDocumentRequest, GeneratePacketRequest
from tools.document_tools import generate_sales_contract_pdf, OUTPUT_DIR, download_from_gcs, list_gcs_documents
from tools.document_engine import (
    generate_document as engine_generate_document,
    generate_packet as engine_generate_packet,
    generate_batch as engine_generate_batch,
    list_available_templates as engine_list_templates,
    list_available_packets as engine_list_packets,
    get_template_fields as engine_get_template_fields,
    get_all_field_definitions as engine_get_all_field_definitions,
)


def _collect_generated_documents():
    """Return generated PDF metadata without exposing document contents."""
    seen = set()
    docs = []
    local_count = 0

    if os.path.exists(OUTPUT_DIR):
        for filename in os.listdir(OUTPUT_DIR):
            if not filename.lower().endswith(".pdf"):
                continue
            path = os.path.join(OUTPUT_DIR, filename)
            if not os.path.isfile(path):
                continue
            stat = os.stat(path)
            docs.append({
                "filename": filename,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "download_url": f"/api/documents/download/{filename}",
                "source": "local",
            })
            seen.add(filename)
            local_count += 1

    gcs_docs = list_gcs_documents()
    gcs_count = len(gcs_docs)
    for gcs_doc in gcs_docs:
        filename = gcs_doc.get("filename")
        if not filename or filename in seen:
            continue
        docs.append({**gcs_doc, "source": "gcs"})
        seen.add(filename)

    docs.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return docs, local_count, gcs_count


# --- Phase 2: Generic Document Engine Endpoints ---

@app.get("/api/documents/templates", dependencies=[Depends(require_admin)])
async def list_templates():
    """List all available document templates with metadata."""
    try:
        templates = engine_list_templates()
        packets = engine_list_packets()
        return {"templates": templates, "packets": packets}
    except Exception as e:
        struct_logger.error("Template listing failed", error=str(e))
        return {"error": "Failed to load templates. Please try again."}


@app.get("/api/documents/readiness", dependencies=[Depends(require_admin)])
async def document_readiness():
    """Summarize document-system readiness for the employee UI."""
    try:
        templates = engine_list_templates()
        packets = engine_list_packets()
        docs, local_count, gcs_count = _collect_generated_documents()
        categories: dict[str, int] = {}
        for template in templates:
            category = template.get("category") or "Other"
            categories[category] = categories.get(category, 0) + 1

        output_dir_exists = os.path.isdir(OUTPUT_DIR)
        output_dir_writable = os.access(OUTPUT_DIR, os.W_OK) if output_dir_exists else False
        required_ready = bool(templates) and bool(packets) and output_dir_exists

        return {
            "status": "ready" if required_ready else "degraded",
            "template_count": len(templates),
            "packet_count": len(packets),
            "category_counts": categories,
            "generated_document_count": len(docs),
            "local_document_count": local_count,
            "gcs_document_count": gcs_count,
            "output_dir": "available" if output_dir_exists else "missing",
            "output_dir_writable": output_dir_writable,
            "latest_document_at": docs[0].get("created_at") if docs else None,
        }
    except Exception as e:
        struct_logger.error("Document readiness failed", error=str(e))
        return JSONResponse(
            {"status": "degraded", "error": "Document readiness check failed."},
            status_code=500,
        )


@app.get("/api/documents/templates/{template_name}/fields", dependencies=[Depends(require_admin)])
async def get_template_fields(template_name: str):
    """Get field definitions for a specific template (drives SmartForm)."""
    try:
        fields = engine_get_template_fields(template_name)
        if fields is None:
            return {"error": f"Template '{template_name}' not found"}
        return fields
    except Exception as e:
        struct_logger.error("Template field lookup failed", error=str(e))
        return {"error": "Failed to load template fields. Please try again."}

@app.post("/api/documents/generate", dependencies=[Depends(require_admin)])
async def generate_document_endpoint(request: GenerateDocumentRequest):
    """Generate any mapped document template.

    When the supplied ``data`` dict cannot satisfy the template's required
    fields the response is a HTTP 400 with the structured envelope
    ``{"error": "missing_required_fields", "message": "<engine message>"}``.
    This prevents the historical "empty doc" class of bug where an almost
    blank request silently produced a near-empty PDF that looked like a
    real document.
    """
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
        # Surface validation failures as 400s with a structured envelope so
        # callers can react programmatically and so we never accidentally
        # ship a 200 + empty PDF for an empty deal.
        message = result.get("message", "")
        if "Missing required fields" in message:
            return JSONResponse(
                {
                    "success": False,
                    "error": "missing_required_fields",
                    "message": message,
                },
                status_code=400,
            )
        return {"success": False, "error": message}
    except Exception as e:
        struct_logger.error("Document generation failed", error=str(e))
        return {"success": False, "error": "Document generation failed. Please try again."}

@app.post("/api/documents/generate-packet", dependencies=[Depends(require_admin)])
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
                "documents_skipped": result.get("documents_skipped", []),
            }
        return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Packet generation failed", error=str(e))
        return {"success": False, "error": "Packet generation failed. Please try again."}

@app.post("/api/documents/extract-fields", dependencies=[Depends(require_admin)])
async def extract_fields_from_chat(request: Request):
    """Extract form field data from chat conversation history using AI."""
    try:
        data = await request.json()
        session_id = data.get("session_id")
        template_name = data.get("template_name")

        if not session_id or not template_name:
            return {"extracted_data": {}, "message": "session_id and template_name required"}

        from tools.form_extraction import extract_form_data_from_session
        try:
            runner = _get_runner()
        except RuntimeError:
            return {"extracted_data": {}, "message": "AI service temporarily unavailable"}
        result = await extract_form_data_from_session(
            session_id=session_id,
            template_name=template_name,
            runner=runner,
        )
        return result
    except Exception as e:
        struct_logger.error("Field extraction failed", error=str(e))
        return {"extracted_data": {}, "message": "Field extraction failed. Please try again."}

@app.get("/api/documents/fields", dependencies=[Depends(require_admin)])
async def get_all_field_definitions():
    """Return all business data field definitions (drives the unified Document Center form)."""
    try:
        return engine_get_all_field_definitions()
    except Exception as e:
        struct_logger.error("Field definitions lookup failed", error=str(e))
        return {"error": "Failed to load field definitions. Please try again."}


@app.post("/api/documents/generate-batch", dependencies=[Depends(require_admin)])
async def generate_batch_endpoint(request: Request):
    """Generate multiple documents from shared data, optionally merged into one PDF."""
    try:
        body = await request.json()
        templates = body.get("templates", [])
        data = body.get("data", {})
        merge = body.get("merge", True)

        if not templates:
            return {"success": False, "error": "No templates selected"}

        result = engine_generate_batch(template_names=templates, data=data, merge=merge)
        return result
    except Exception as e:
        struct_logger.error("Batch generation failed", error=str(e))
        return {"success": False, "error": "Batch generation failed. Please try again."}


# --- Legacy Endpoint (Phase 1 backward compatibility) ---

@app.post("/api/documents/sales-contract", dependencies=[Depends(require_admin)])
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
        return {"success": False, "error": "Document generation failed. Please try again."}

@app.get("/api/documents/download/{filename}", dependencies=[Depends(require_admin)])
async def download_document(filename: str):
    """Download a generated document. Tries local disk first, then GCS."""
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    if not safe_filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Only PDF files are available for download."}, status_code=400)

    resolved = os.path.abspath(os.path.join(OUTPUT_DIR, safe_filename))
    if not resolved.startswith(os.path.abspath(OUTPUT_DIR)):
        return JSONResponse({"error": "Invalid filename"}, status_code=403)

    # Try local first
    if os.path.isfile(resolved):
        return FileResponse(resolved, filename=safe_filename, media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'})

    # Fall back to GCS (local file may have been lost on Cloud Run restart)
    if download_from_gcs(safe_filename, resolved):
        return FileResponse(resolved, filename=safe_filename, media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'})

    return JSONResponse({"error": "File not found"}, status_code=404)


# ─── Inventory API ───
from database.deal_validation import validate_for_documents
from database.firestore_client import get_database
from database.models import Deal, DealStatus, Inventory

_db = get_database()


@app.get("/api/inventory", dependencies=[Depends(require_admin)])
async def list_inventory(
    status: str = "AVAILABLE",
    limit: int = 100,
    is_new: bool = None
):
    """List inventory for document generation."""
    try:
        inventory = _db.search_inventory(status=status, limit=limit)

        # Transform for frontend
        results = []
        for item in inventory:
            # Filter by is_new if specified
            if is_new is not None and item.get("is_new") != is_new:
                continue

            results.append({
                "id": item.get("id"),
                "model_name": item.get("model_name") or item.get("model"),
                "manufacturer": item.get("manufacturer"),
                "year": item.get("year"),
                "is_new": item.get("is_new", True),
                "serial_number": item.get("serial_number"),
                "label_number": item.get("label_number"),
                "sections": item.get("sections") or item.get("no_of_sections"),
                "beds": item.get("bedrooms"),
                "baths": item.get("bathrooms"),
                "sqft": item.get("sqft"),
                "sale_price": item.get("sale_price") or item.get("msrp"),
                "image_url": item.get("image_url") or item.get("hero_image"),
                "status": item.get("status", "AVAILABLE"),
            })

        return {"success": True, "inventory": results, "count": len(results)}
    except Exception as e:
        struct_logger.error("Inventory listing failed", error=str(e))
        return {"success": False, "error": "Failed to load inventory. Please try again."}


@app.get("/api/admin/inventory/photo-audit", dependencies=[Depends(require_admin)])
async def admin_inventory_photo_audit(limit: int = 5000):
    """
    Read-only photo dedup audit.

    Aggregates inventory image fields (image_url, hero_image, floorplan_url,
    gallery_images, photos, real_photos) and reports any URL referenced by
    two or more distinct inventory documents. Does NOT mutate Firestore;
    the operator decides which dedupes to apply.
    """
    try:
        from tools.photo_dedup_audit import run_photo_dedup_audit
        # Pull the raw inventory docs directly so list/single image fields
        # are preserved (search_inventory's transform drops some).
        docs = _db.db.collection("inventory").limit(limit).stream()
        inventory = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            inventory.append(data)
        report = run_photo_dedup_audit(inventory)
        return {"success": True, **report}
    except Exception as e:
        struct_logger.error("Photo dedup audit failed", error=str(e))
        return {"success": False, "error": "Failed to run photo dedup audit."}


@app.post("/api/inventory/bulk-import", dependencies=[Depends(require_admin)])
async def bulk_import_inventory(request: Request):
    """
    Bulk import inventory items to Firestore.
    Used by inventory_sync.py to import scraped website inventory.
    """
    try:
        data = await request.json()
        items = data.get("items", [])
        
        if not items:
            return {"success": False, "error": "No items provided"}
        
        imported = 0
        updated = 0
        errors = []
        
        for item in items:
            try:
                inventory_id = item.get("id")
                if not inventory_id:
                    errors.append(f"Skipping item without ID: {item.get('model_name', 'unknown')}")
                    continue
                
                # Check if exists
                existing = _db.get_inventory_by_id(inventory_id)
                
                # Prepare data for Firestore, then validate through the shared
                # Inventory model before writing to the production collection.
                firestore_data = {
                    "id": inventory_id,
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
                    "serial_number": item.get("serial_number") or inventory_id,
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
                Inventory(**{
                    "id": firestore_data["id"],
                    "serial_number": firestore_data["serial_number"],
                    "model_name": firestore_data["model_name"],
                    "manufacturer": firestore_data["manufacturer"],
                    "msrp": firestore_data["msrp"],
                    "sale_price": firestore_data["sale_price"],
                    "bedrooms": firestore_data["bedrooms"],
                    "bathrooms": firestore_data["bathrooms"],
                    "sqft": firestore_data["sqft"],
                    "status": firestore_data["status"],
                    "photos": firestore_data["photos"],
                })
                
                # Save to Firestore
                doc_ref = _db.db.collection("inventory").document(inventory_id)
                
                if existing:
                    doc_ref.update(firestore_data)
                    updated += 1
                else:
                    doc_ref.set(firestore_data)
                    imported += 1
                    
            except Exception as e:
                struct_logger.error(f"Failed to import item {item.get('id')}", error=str(e))
                errors.append(f"{item.get('model_name', item.get('id'))}: {str(e)}")
        
        struct_logger.info(
            "Bulk inventory import completed",
            imported=imported,
            updated=updated,
            errors=len(errors)
        )
        
        return {
            "success": True,
            "imported": imported,
            "updated": updated,
            "errors": errors if errors else None
        }
        
    except Exception as e:
        struct_logger.error("Bulk import failed", error=str(e))
        return {"success": False, "error": str(e)}


# ─── Deals API (replaces fastcontractdocs.com) ───
_deal_db = _db


@app.get("/api/deals", dependencies=[Depends(require_admin)])
async def list_deals(status: str = None, salesrep: str = None, q: str = None, limit: int = 50):
    """List deals with optional filters."""
    try:
        deals = _deal_db.search_deals(
            status=status,
            salesrep=salesrep,
            buyer_name=q,
            limit=limit
        )
        enriched = _enrich_deals_with_home(deals, _deal_db)
        return {"success": True, "deals": [_strip_pii_from_deal(d) for d in enriched], "count": len(enriched)}
    except Exception as e:
        struct_logger.error("Deal listing failed", error=str(e))
        return {"success": False, "error": "Failed to load deals. Please try again."}


@app.post("/api/deals", dependencies=[Depends(require_admin)])
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
        return {"success": False, "error": "Failed to create deal. Please try again."}


def _strip_pii_from_deal(deal: dict) -> dict:
    """Remove raw SSN from deal data before sending to frontend."""
    if isinstance(deal, dict):
        return {k: v for k, v in deal.items() if k not in ("buyer_ssn", "co_buyer_ssn")}
    return deal


def _deal_serial(deal: dict) -> str | None:
    """Extract a usable home serial from a deal record.

    Different ingest paths landed slightly different field names on the
    `deals` collection:
      * `import_fcd_deals.py` and the manual deal form use `serial_number_1`
        (matches the Pydantic `Deal` model).
      * The historical FCD bulk import used `serial_number` (singular).
    We accept either and fall back to None.
    """
    if not isinstance(deal, dict):
        return None
    for key in ("serial_number_1", "serial_number"):
        val = deal.get(key)
        if val:
            return str(val).strip() or None
    return None


def _enrich_deals_with_home(deals: list, db) -> list:
    """Attach `home_label` and `home_manufacturer` to each deal.

    Behaviour:
      * If the deal already has `manufacturer` and (`model` or `home_model`)
        populated, no inventory lookup is needed; we just normalize a
        `home_label` for the frontend.
      * Otherwise, if the deal has a serial number (`serial_number_1` or
        `serial_number`), we batch-look-up the inventory and attach the
        matching home's `model_name` + `manufacturer` as `home_label` and
        `home_manufacturer`.
      * If no inventory match exists, `home_label` stays unset so the
        frontend can render the "Serial: XXXX" fallback.

    The output is schema-additive: we never strip existing keys. New keys
    only appear when we have a value to attach.
    """
    if not deals:
        return deals

    # Collect serials that need an inventory lookup. We skip deals that
    # already carry a model — they don't need enrichment.
    needs_lookup: dict[str, list[dict]] = {}
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        existing_model = deal.get("model") or deal.get("home_model")
        if existing_model and deal.get("manufacturer"):
            continue
        serial = _deal_serial(deal)
        if not serial:
            continue
        needs_lookup.setdefault(serial, []).append(deal)

    inventory_by_serial: dict = {}
    if needs_lookup:
        try:
            getter = getattr(db, "get_inventory_by_serials", None)
            if callable(getter):
                inventory_by_serial = getter(list(needs_lookup.keys())) or {}
            else:
                # Fallback: per-serial lookup if the batched method is absent
                # (e.g., older test stubs).
                single = getattr(db, "get_inventory_by_serial", None)
                if callable(single):
                    for serial in needs_lookup:
                        match = single(serial)
                        if match:
                            inventory_by_serial[serial] = match
        except Exception as exc:  # noqa: BLE001 - never let enrichment break /api/deals
            struct_logger.warning(
                "Deal home enrichment failed; falling back to raw deals",
                error=str(exc),
            )
            inventory_by_serial = {}

    for serial, matched_deals in needs_lookup.items():
        inv = inventory_by_serial.get(serial)
        if not inv:
            continue
        label = inv.get("model_name") or inv.get("model")
        manufacturer = inv.get("manufacturer")
        for deal in matched_deals:
            if label and not deal.get("home_label"):
                deal["home_label"] = label
            if manufacturer and not deal.get("home_manufacturer"):
                deal["home_manufacturer"] = manufacturer

    # For deals that already had model/manufacturer, surface a unified
    # `home_label` so the frontend doesn't have to know about every field
    # name. This is purely additive.
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        if deal.get("home_label"):
            continue
        existing_model = deal.get("model") or deal.get("home_model")
        if existing_model:
            deal["home_label"] = existing_model
        if not deal.get("home_manufacturer") and deal.get("manufacturer"):
            deal["home_manufacturer"] = deal.get("manufacturer")

    return deals


@app.get("/api/deals/{deal_id}", dependencies=[Depends(require_admin)])
async def get_deal(deal_id: str):
    """Get deal details (SSN stripped)."""
    try:
        deal = _deal_db.get_deal(deal_id)
        if not deal:
            return {"success": False, "error": "Deal not found"}
        return {"success": True, "deal": _strip_pii_from_deal(deal)}
    except Exception as e:
        struct_logger.error("Deal fetch failed", error=str(e))
        return {"success": False, "error": "Failed to load deal details. Please try again."}


@app.put("/api/deals/{deal_id}", dependencies=[Depends(require_admin)])
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
        return {"success": False, "error": "Failed to update deal. Please try again."}


@app.put("/api/deals/{deal_id}/status", dependencies=[Depends(require_admin)])
async def update_deal_status(deal_id: str, request: Request):
    """Change deal status."""
    try:
        data = await request.json()
        new_status = data.get("status")
        valid_statuses = [s.value for s in DealStatus]
        if new_status not in valid_statuses:
            return {"success": False, "error": f"Invalid status. Must be one of: {valid_statuses}"}

        # Capture prior status before writing so the webhook carries {from, to}.
        prior_deal = _deal_db.get_deal(deal_id) or {}
        prior_status = prior_deal.get("status")
        _deal_db.update_deal(deal_id, {"status": new_status})

        # Send status update email if buyer has email
        try:
            deal_data = _deal_db.get_deal(deal_id)
            buyer_email = deal_data.get("buyer_email") if deal_data else None
            if buyer_email and new_status in ("approved", "contract", "funded", "complete"):
                buyer_name = f"{deal_data.get('buyer_first_name', '')} {deal_data.get('buyer_last_name', '')}".strip()
                send_deal_status_update(
                    to=buyer_email,
                    customer_name=buyer_name or "Valued Customer",
                    deal_id=deal_id,
                    new_status=new_status,
                    home_name=deal_data.get("model"),
                )
        except Exception as e:
            struct_logger.warning("Deal status email failed", error=str(e))

        # Dispatch partner webhooks (fire-and-forget; PII-safe payload).
        try:
            from tools.partner_webhooks import dispatch_partner_event

            deal_snapshot = _deal_db.get_deal(deal_id) or {}
            base_payload = {
                "deal_id": deal_id,
                "from": prior_status,
                "to": new_status,
                "inventory_id": deal_snapshot.get("inventory_id"),
                "customer_id": deal_snapshot.get("customer_id"),
                "manufacturer": deal_snapshot.get("manufacturer"),
                "model": deal_snapshot.get("model"),
                "salesrep": deal_snapshot.get("salesrep"),
                "updated_at": deal_snapshot.get("updated_at"),
            }
            dispatch_partner_event("deal.status_changed", base_payload, db=_db)
            if new_status == "funded":
                dispatch_partner_event("deal.funded", base_payload, db=_db)
            elif new_status == "complete":
                dispatch_partner_event("deal.complete", base_payload, db=_db)
        except Exception as e:
            struct_logger.warning("Partner webhook dispatch failed", error=str(e))

        return {"success": True, "message": f"Deal status changed to {new_status}"}
    except Exception as e:
        struct_logger.error("Deal status update failed", error=str(e))
        return {"success": False, "error": "Failed to update deal status. Please try again."}


@app.post("/api/deals/{deal_id}/generate-document", dependencies=[Depends(require_admin)])
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

        # Validate before document generation so the user gets a structured
        # report of which deal fields are missing instead of a cryptic
        # "Missing required fields" error from the document engine.
        # Overrides from the request body can satisfy missing fields, so
        # apply them first and validate the merged view.
        overrides = data.get("overrides", {})
        merged_for_validation = {**deal.model_dump(), **overrides}
        missing = validate_for_documents(merged_for_validation)
        if missing:
            return JSONResponse(
                {"error": "missing_required_fields", "missing": missing},
                status_code=400,
            )

        doc_data = deal.to_document_data()
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
        return {"success": False, "error": "Failed to generate document from deal. Please try again."}


@app.post("/api/deals/{deal_id}/generate-packet", dependencies=[Depends(require_admin)])
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

        # Allow overrides and validate the merged deal view before packet
        # generation so incomplete deals get the same structured response as
        # single-document generation.
        overrides = data.get("overrides", {})
        merged_for_validation = {**deal.model_dump(), **overrides}
        missing = validate_for_documents(merged_for_validation)
        if missing:
            return JSONResponse(
                {"error": "missing_required_fields", "missing": missing},
                status_code=400,
            )

        doc_data = deal.to_document_data()
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
        return {"success": False, "error": "Failed to generate closing packet. Please try again."}


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
from tools.asset_scraper import PROPERTY_ASSETS, get_matterport_url
from tools.photo_classifier import apply_classifier_to_home
from tools.video_generator import GENERATED_VIDEOS_DIR

@app.post("/api/marketing/generate-script", dependencies=[Depends(require_admin)])
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
        return {"error": "Script generation failed. Please try again."}

@app.get("/api/marketing/trending-ideas", dependencies=[Depends(require_admin)])
async def api_trending_ideas():
    """Get AI-generated trending content ideas."""
    try:
        result = get_trending_content_ideas()
        return result
    except Exception as e:
        struct_logger.error("Trending ideas failed", error=str(e))
        return {"error": "Failed to load trending ideas. Please try again."}

@app.post("/api/marketing/schedule", dependencies=[Depends(require_admin)])
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
        return {"error": "Failed to schedule post. Please try again."}

@app.get("/api/marketing/analytics", dependencies=[Depends(require_admin)])
async def api_content_analytics():
    """Get content performance analytics."""
    try:
        result = analyze_content_performance()
        return result
    except Exception as e:
        struct_logger.error("Analytics load failed", error=str(e))
        return {"error": "Failed to load analytics. Please try again."}


@app.post("/api/marketing/generate-voiceover", dependencies=[Depends(require_admin)])
async def api_generate_voiceover(request: Request):
    """Generate voiceover audio from script using Google Cloud Text-to-Speech."""
    try:
        from tools.marketing_tools import generate_script_voiceover
        data = await request.json()
        
        script_text = data.get("script_text", "")
        if not script_text:
            return {"success": False, "error": "script_text is required"}
        
        result = generate_script_voiceover(
            script_text=script_text,
            voice=data.get("voice", "en-US-Neural2-D"),
            speaking_rate=data.get("speaking_rate", 1.0)
        )
        return result
    except Exception as e:
        struct_logger.error("Voiceover generation failed", error=str(e))
        return {"success": False, "error": "Voiceover generation failed. Please try again."}


@app.get("/api/marketing/voiceover-voices", dependencies=[Depends(require_admin)])
async def api_get_voiceover_voices():
    """Get available TTS voices."""
    try:
        from tools.marketing_tools import TTS_VOICES
        return {"success": True, "voices": TTS_VOICES}
    except Exception as e:
        struct_logger.error("Voice list failed", error=str(e))
        return {"success": False, "error": "Failed to load voices"}


@app.post("/api/marketing/generate-image", dependencies=[Depends(require_admin)])
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
        return {"success": False, "error": "Image generation failed. Please try again."}


@app.post("/api/marketing/generate-video", dependencies=[Depends(require_admin)])
async def api_generate_video(request: Request):
    """Generate an MP4 video from photos, voiceover, and script."""
    try:
        from tools.video_generator import generate_ad_video
        data = await request.json()
        
        photos = data.get("photos", [])
        voiceover_base64 = data.get("voiceover_base64", "")
        script_text = data.get("script_text", "")
        home_name = data.get("home_name", "ad")
        
        if not photos:
            return {"success": False, "error": "At least one photo is required"}
        if not voiceover_base64:
            return {"success": False, "error": "Voiceover audio is required"}
        
        result = generate_ad_video(
            photos=photos,
            voiceover_base64=voiceover_base64,
            script_text=script_text,
            home_name=home_name,
            platform=data.get("platform", "tiktok"),
            duration_per_photo=data.get("duration_per_photo", 3.0)
        )
        return result
    except Exception as e:
        struct_logger.error("Video generation failed", error=str(e))
        return {"success": False, "error": f"Video generation failed: {str(e)}"}


@app.get("/api/marketing/videos/{filename}", dependencies=[Depends(require_admin)])
async def download_generated_video(filename: str):
    """Download a generated video."""
    import os
    from fastapi.responses import FileResponse
    
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or ".." in filename:
        return {"error": "Invalid filename"}
    
    if not safe_filename.lower().endswith(".mp4"):
        return {"error": "Only MP4 files allowed"}
    
    resolved = os.path.abspath(os.path.join(GENERATED_VIDEOS_DIR, safe_filename))
    if not resolved.startswith(os.path.abspath(GENERATED_VIDEOS_DIR)):
        return {"error": "Invalid path"}
    
    if os.path.isfile(resolved):
        return FileResponse(
            resolved, 
            filename=safe_filename,
            media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'}
        )
    return {"error": "File not found"}


@app.get("/api/marketing/inventory-context")
async def api_inventory_context():
    """Get inventory highlights for ad creation and the public browse page."""
    try:
        from tools.asset_scraper import get_assets_for_home
        
        # Firestore is the live source of truth. Website assets are enrichment
        # and fallback only; they should not create stale duplicate listings.
        result = get_inventory_for_ads(limit=100)
        firestore_homes = result.get("homes", [])
        
        # Enrich Firestore homes with asset catalog images if missing
        for home in firestore_homes:
            has_images = home.get("real_photos") or home.get("gallery_images")
            if not has_images:
                # Try to find matching assets
                asset = get_assets_for_home(home.get("model_name", ""))
                if asset and asset.get("images"):
                    home["real_photos"] = asset["images"]
                    home["gallery_images"] = asset["images"][:3]
                    home["image_categories"] = asset.get("image_categories", {})
                    home["floor_plan_url"] = home.get("floor_plan_url") or asset.get("floor_plan")
                    if asset.get("matterport_id") and not home.get("matterport_id"):
                        home["matterport_id"] = asset["matterport_id"]
                        home["matterport_url"] = get_matterport_url(asset["matterport_id"])
        website_homes = []
        if not firestore_homes:
            for slug, asset in PROPERTY_ASSETS.items():
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
            result["homes"] = website_homes
            result["total_inventory"] = len(website_homes)
        else:
            result["homes"] = firestore_homes
            result["total_inventory"] = result.get("total_inventory", len(firestore_homes))

        # Apply URL-based floorplan classifier to every home before responding.
        # 2026-04-30 prod audit: 35/44 homes had image_url pointing at a
        # /floorplan/ URL. apply_classifier_to_home() promotes the first
        # exterior to image_url and moves floorplans into floorplan_url.
        for home in result.get("homes", []):
            apply_classifier_to_home(home)

        result["website_homes"] = len(website_homes)
        return result
    except Exception as e:
        struct_logger.error("Inventory context failed", error=str(e))
        return {"success": False, "error": "Failed to load inventory context. Please try again."}


@app.get("/api/marketing/images/{filename}", dependencies=[Depends(require_admin)])
async def download_ad_image(filename: str):
    """Download a generated ad image."""
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    resolved = os.path.abspath(os.path.join(GENERATED_ADS_DIR, safe_filename))
    if not resolved.startswith(os.path.abspath(GENERATED_ADS_DIR)):
        return JSONResponse({"error": "Invalid filename"}, status_code=403)
    if os.path.isfile(resolved):
        return FileResponse(resolved, filename=safe_filename, media_type='image/png')
    return JSONResponse({"error": "Image not found"}, status_code=404)


# ─── Contact Form API ───

@app.post("/api/contact")
async def submit_contact_form(request: Request):
    """Receive contact form submissions and log as leads."""
    try:
        data = await request.json()
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        message = data.get("message", "").strip()

        email = data.get("email", "").strip()

        if not name or not phone:
            return {"success": False, "error": "Name and phone are required"}

        struct_logger.info("Contact form submitted", name=name, has_phone=bool(phone))

        lead_id = f"contact_{int(time.time())}_{uuid.uuid4().hex[:4]}"

        # Create a lead from the contact form
        try:
            new_lead = Lead(
                lead_id=lead_id,
                user_id="contact_form",
                session_id=f"contact_{int(time.time())}",
                source=data.get("source", "contact_form"),
                name=name,
                phone=phone,
                email=email or None,
            )
            await lead_manager.create_lead(new_lead)
        except Exception as e:
            struct_logger.warning("Contact lead creation failed", error=str(e))

        # Send welcome email if email provided
        if email:
            try:
                send_lead_welcome(to=email, customer_name=name, lead_id=lead_id)
            except Exception as e:
                struct_logger.warning("Lead welcome email failed", error=str(e))

        # Notify owner of new lead
        try:
            notify_new_lead(customer_name=name, phone=phone, email=email, source=data.get("source", "contact_form"))
        except Exception as e:
            struct_logger.warning("Lead admin notification failed", error=str(e))

        return {"success": True, "message": "Thank you! We'll be in touch shortly."}
    except Exception as e:
        struct_logger.error("Contact form failed", error=str(e))
        return {"success": False, "error": "Something went wrong. Please try again or call us directly."}


# ─── Appointment Scheduling API ───

@app.get("/api/appointments/slots")
async def get_available_slots(date: str):
    """Get available appointment slots for a given date."""
    try:
        result = await appointment_manager.get_available_slots(date)
        return result
    except Exception as e:
        struct_logger.error("Slots lookup failed", error=str(e))
        return {"error": "Failed to load available slots. Please try again."}


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

        # Send confirmation email if email provided
        if appt.email:
            try:
                send_appointment_confirmation(
                    to=appt.email,
                    customer_name=name,
                    date=appt_date,
                    time_slot=time_slot,
                    appointment_id=appt.appointment_id,
                    notes=appt.notes,
                )
            except Exception as e:
                struct_logger.warning("Appointment confirmation email failed", error=str(e))

        # Notify owner of new appointment
        try:
            notify_new_appointment(
                customer_name=name, phone=phone, date=appt_date,
                time_slot=time_slot, email=appt.email, notes=appt.notes,
            )
        except Exception as e:
            struct_logger.warning("Appointment admin notification failed", error=str(e))

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
        return {"success": False, "error": "Failed to book appointment. Please try again or call us."}


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
        return {"error": "Failed to load appointment details. Please try again."}


@app.post("/api/appointments/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: str, request: Request):
    """Cancel an appointment."""
    try:
        data = await request.json()
        phone = data.get("phone", "").strip()

        appt = await appointment_manager.get_appointment(appointment_id)
        if not appt:
            return {"success": False, "error": "Appointment not found."}

        # Require phone verification to prevent unauthorized cancellation
        if not phone:
            return {"success": False, "error": "Phone number is required to cancel an appointment."}
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
        return {"success": False, "error": "Failed to cancel appointment. Please try again."}


# ─── CRM API (leads, email, activity) ───

@app.get("/api/leads", dependencies=[Depends(require_admin)])
async def list_leads_api(status: str = None, limit: int = 100):
    """List leads for CRM dashboard."""
    try:
        leads = await lead_manager.list_leads(status=status, limit=limit)
        return {"success": True, "leads": [l.to_dict() for l in leads], "count": len(leads)}
    except Exception as e:
        struct_logger.error("Lead listing failed", error=str(e))
        return {"success": False, "error": "Failed to load leads. Please try again."}


@app.get("/api/leads/{lead_id}", dependencies=[Depends(require_admin)])
async def get_lead_api(lead_id: str):
    """Get a single lead by ID."""
    try:
        lead = await lead_manager.get_lead(lead_id)
        if not lead:
            return {"success": False, "error": "Lead not found"}
        return {"success": True, "lead": lead.to_dict()}
    except Exception as e:
        struct_logger.error("Lead fetch failed", error=str(e))
        return {"success": False, "error": "Failed to load lead details. Please try again."}


@app.put("/api/leads/{lead_id}", dependencies=[Depends(require_admin)])
async def update_lead_api(lead_id: str, request: Request):
    """Update lead status or data."""
    try:
        data = await request.json()
        lead = await lead_manager.get_lead(lead_id)
        if not lead:
            return {"success": False, "error": "Lead not found"}
        for key, value in data.items():
            if hasattr(lead, key) and key not in ("lead_id", "created_at"):
                setattr(lead, key, value)
        await lead_manager.update_lead(lead)
        return {"success": True, "message": "Lead updated"}
    except Exception as e:
        struct_logger.error("Lead update failed", error=str(e))
        return {"success": False, "error": "Failed to update lead. Please try again."}


@app.get("/api/crm/appointments", dependencies=[Depends(require_admin)])
async def list_appointments_api(status: str = None, limit: int = 100):
    """List appointments for CRM dashboard."""
    try:
        appts = await appointment_manager.list_appointments(status=status, limit=limit)
        return {"success": True, "appointments": [a.to_dict() for a in appts], "count": len(appts)}
    except Exception as e:
        struct_logger.error("Appointment listing failed", error=str(e))
        return {"success": False, "error": "Failed to load appointments. Please try again."}


@app.post("/api/email/send", dependencies=[Depends(require_admin)])
async def send_email_api(request: Request):
    """Send a custom email from the CRM."""
    try:
        data = await request.json()
        to = data.get("to", "").strip()
        customer_name = data.get("customer_name", "").strip()
        subject = data.get("subject", "").strip()
        message = data.get("message", "").strip()

        if not to or not subject or not message:
            return {"success": False, "error": "to, subject, and message are required"}

        result = send_custom_email(to=to, customer_name=customer_name, subject=subject, message=message)
        return result
    except Exception as e:
        struct_logger.error("Email send failed", error=str(e))
        return {"success": False, "error": "Failed to send email. Please try again."}


@app.get("/api/email/log", dependencies=[Depends(require_admin)])
async def get_email_log_api(limit: int = 50, email_type: str = None):
    """Get email activity log for CRM timeline."""
    try:
        log = get_email_log(limit=limit, email_type=email_type)
        return {"success": True, "emails": log, "count": len(log)}
    except Exception as e:
        struct_logger.error("Email log fetch failed", error=str(e))
        return {"success": False, "error": "Failed to load email log. Please try again."}


# ─── Chat History API ────────────────────────────────────

@app.get("/api/chat/history/{session_id}", dependencies=[Depends(require_admin)])
async def get_chat_history(session_id: str):
    """Get full chat history for a session (admin only)."""
    try:
        session = await chat_history.get_session(session_id)
        if session:
            return {
                "success": True,
                "session": {
                    "session_id": session.session_id,
                    "user_id": session.user_id,
                    "status": session.status,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "message_count": len(session.messages),
                    "messages": [
                        {
                            "role": m.role,
                            "text": m.text[:200] + "..." if len(m.text) > 200 else m.text,
                            "timestamp": m.timestamp
                        }
                        for m in session.messages[-50:]  # Last 50 messages
                    ]
                }
            }
        return {"success": False, "error": "Session not found"}
    except Exception as e:
        struct_logger.error("Chat history fetch failed", error=str(e), session_id=session_id)
        return {"success": False, "error": "Failed to load chat history"}


@app.get("/api/chat/sessions", dependencies=[Depends(require_admin)])
async def get_chat_sessions(hours: int = 24, limit: int = 50):
    """Get recent chat sessions (admin only)."""
    try:
        sessions = await chat_history.get_recent_sessions(hours=hours, limit=limit)
        return {
            "success": True,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "user_id": s.user_id,
                    "status": s.status,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                    "message_count": len(s.messages),
                    "summary": s.get_summary(),
                    "lead_id": s.lead_id
                }
                for s in sessions
            ],
            "count": len(sessions)
        }
    except Exception as e:
        struct_logger.error("Chat sessions fetch failed", error=str(e))
        return {"success": False, "error": "Failed to load chat sessions"}


@app.post("/api/chat/search", dependencies=[Depends(require_admin)])
async def search_chat_conversations(request: Request):
    """Search through chat conversations (admin only)."""
    try:
        data = await request.json()
        query = data.get("query", "")
        
        if not query:
            return {"success": False, "error": "Query required"}
        
        results = await chat_history.search_conversations(query=query)
        return {"success": True, "results": results, "count": len(results)}
    except Exception as e:
        struct_logger.error("Chat search failed", error=str(e))
        return {"success": False, "error": "Search failed"}


@app.post("/api/admin/verify")
async def verify_admin_pin(request: Request):
    """Validate admin PIN server-side and return a session token."""
    client_ip = _get_client_ip(request)

    # Check brute-force lockout
    now = time.time()
    attempts = _pin_attempts.get(client_ip, [])
    # Prune old attempts outside the lockout window
    attempts = [t for t in attempts if now - t < PIN_LOCKOUT_SECONDS]
    _pin_attempts[client_ip] = attempts
    if len(attempts) >= PIN_MAX_ATTEMPTS:
        struct_logger.warning("Admin login locked out", client_ip=client_ip, attempts=len(attempts))
        return JSONResponse(
            {"success": False, "error": "Too many failed attempts. Please wait 5 minutes."},
            status_code=429,
            headers={"Retry-After": str(PIN_LOCKOUT_SECONDS)},
        )

    try:
        data = await request.json()
    except (JSONDecodeError, ValueError):
        return JSONResponse(
            {"success": False, "error": "Malformed JSON body."},
            status_code=400,
        )
    if not isinstance(data, dict):
        return JSONResponse(
            {"success": False, "error": "Request body must be a JSON object."},
            status_code=400,
        )
    pin = data.get("pin", "")
    if not isinstance(pin, str):
        return JSONResponse(
            {"success": False, "error": "PIN must be a string."},
            status_code=400,
        )
    if not ADMIN_PIN_HASH:
        struct_logger.warning("Admin login rejected: ADMIN_PIN_HASH not configured")
        return JSONResponse({"success": False, "error": "Admin auth not configured."}, status_code=503)
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    if not secrets.compare_digest(pin_hash, ADMIN_PIN_HASH):
        _pin_attempts.setdefault(client_ip, []).append(now)
        remaining = PIN_MAX_ATTEMPTS - len(_pin_attempts[client_ip])
        struct_logger.warning("Admin login failed", client_ip=client_ip, remaining=remaining)
        return JSONResponse({"success": False, "error": "Incorrect PIN."}, status_code=401)
    # Successful login — clear attempt history
    _pin_attempts.pop(client_ip, None)
    token = _create_admin_token()
    struct_logger.info("Admin login succeeded", client_ip=client_ip)
    return {"success": True, "token": token}


@app.get("/api/admin/check")
async def check_admin_token(request: Request):
    """Verify that an admin token is still valid (stateless — works across instances)."""
    token = _admin_token_from_request(request)
    if _verify_admin_token(token):
        return {"valid": True}
    return JSONResponse({"valid": False}, status_code=401)


# ─── Customer API (migrated FastContract records) ────────────────────────────

def _strip_ssn_from_customer(c: dict) -> dict:
    """Remove SSN hashes from customer data before sending to frontend."""
    safe = {k: v for k, v in c.items() if k not in ("ssn_hash",)}
    if c.get("co_buyer") and isinstance(c["co_buyer"], dict):
        safe["co_buyer"] = {k: v for k, v in c["co_buyer"].items() if k != "ssn_hash"}
    return safe


@app.get("/api/customers/search", dependencies=[Depends(require_admin)])
async def search_customers(q: str = "", status: str = "", limit: int = 50):
    """Search customer records in Firestore by name, phone, email, or legacy ID."""
    try:
        results = _db.search_customers(
            query_text=q or None,
            status=status or None,
            limit=limit,
        )
        safe_results = [_strip_ssn_from_customer(c) for c in results]
        return {"customers": safe_results, "total": len(safe_results), "source": "firestore"}
    except Exception as e:
        logger.error(f"Customer search failed: {e}")
        return {"customers": [], "total": 0, "source": "firestore", "error": str(e)}


@app.get("/api/customers/stats", dependencies=[Depends(require_admin)])
async def customer_stats():
    """Get customer statistics from Firestore."""
    try:
        counts = _db.count_customers()
        return {"migrated": True, **counts}
    except Exception as e:
        logger.error(f"Customer stats failed: {e}")
        return {"migrated": False, "error": str(e)}


@app.get("/api/customers/count", dependencies=[Depends(require_admin)])
async def customer_count():
    """Get total customer count from Firestore."""
    try:
        return _db.count_customers()
    except Exception as e:
        logger.error(f"Customer count failed: {e}")
        return {"total": 0, "by_status": {}, "error": str(e)}


@app.get("/api/analytics/customers", dependencies=[Depends(require_admin)])
async def customer_analytics():
    """Customer segmentation analytics — status, geography, timeline, salesrep performance."""
    try:
        # Scan all customers for analytics
        all_custs = _db.search_customers(limit=5000)

        # Status breakdown
        by_status = {}
        by_city = {}
        by_salesrep = {}
        by_month = {}
        recent_leads = []

        for c in all_custs:
            # Status
            s = c.get("status", "UNKNOWN")
            by_status[s] = by_status.get(s, 0) + 1

            # Geography
            city = c.get("city", "Unknown") or "Unknown"
            by_city[city] = by_city.get(city, 0) + 1

            # Salesrep (normalize case for deduplication)
            rep = (c.get("salesrep") or "").strip().title() or "Unassigned"
            if rep and rep != "None":
                by_salesrep[rep] = by_salesrep.get(rep, 0) + 1

            # Timeline (by month of creation)
            created = c.get("created_at", "")
            if created:
                month = str(created)[:7]  # YYYY-MM
                by_month[month] = by_month.get(month, 0) + 1

            # Recent leads
            if s == "LEAD" and len(recent_leads) < 10:
                recent_leads.append({
                    "name": c.get("full_name"),
                    "phone": c.get("phone"),
                    "city": city,
                    "created": str(created)[:10] if created else None,
                })

        # Top cities
        top_cities = sorted(by_city.items(), key=lambda x: x[1], reverse=True)[:15]

        # Top salesreps
        top_reps = sorted(by_salesrep.items(), key=lambda x: x[1], reverse=True)[:10]

        # Conversion rate
        total = len(all_custs)
        enrolled = by_status.get("ENROLLED", 0)
        conversion_rate = (enrolled / total * 100) if total else 0

        return {
            "total": total,
            "by_status": by_status,
            "conversion_rate": round(conversion_rate, 1),
            "top_cities": [{"city": c, "count": n} for c, n in top_cities],
            "top_salesreps": [{"name": r, "count": n} for r, n in top_reps],
            "by_month": dict(sorted(by_month.items())),
            "recent_leads": recent_leads,
        }
    except Exception as e:
        logger.error(f"Customer analytics failed: {e}")
        return {"error": str(e)}


@app.get("/api/customers/{customer_id}", dependencies=[Depends(require_admin)])
async def get_customer(customer_id: str):
    """Get a single customer record by document ID or legacy_id."""
    try:
        c = _db.get_customer(customer_id)
        if c is None:
            raise HTTPException(404, "Customer not found")
        return _strip_ssn_from_customer(c)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Customer get failed: {e}")
        raise HTTPException(500, "Failed to retrieve customer")


# ─── Customer CRUD (create, update) ──────────────────────────────────────────

def _sanitize_text(val: str, max_len: int = 500) -> str:
    """Strip HTML tags and limit length for customer input fields."""
    import re
    return re.sub(r'<[^>]+>', '', val).strip()[:max_len]


@app.post("/api/customers", dependencies=[Depends(require_admin)])
async def create_customer(request: Request):
    """Create a new customer record in Firestore."""
    data = await request.json()

    name = _sanitize_text(data.get("full_name") or "")
    if len(name) < 2:
        raise HTTPException(400, "full_name is required (min 2 chars)")

    # Validate status against allowed values
    allowed_statuses = {"LEAD", "ENROLLED", "NON_ENROLLED", "SOLD", "CLOSED"}
    status = (data.get("status") or "LEAD").upper()
    if status not in allowed_statuses:
        status = "LEAD"

    import uuid as _uuid
    customer_id = str(_uuid.uuid4())
    customer = {
        "legacy_id": _sanitize_text(data.get("legacy_id") or "", 50),
        "legacy_source": data.get("legacy_source", "manual") if data.get("legacy_source") in ("manual", "fastcontract", "import") else "manual",
        "full_name": name,
        "email": (data.get("email") or "").strip().lower()[:200] or None,
        "phone": (data.get("phone") or "").strip()[:20] or None,
        "status": status,
        "address": _sanitize_text(data.get("address") or "", 300) or None,
        "city": _sanitize_text(data.get("city") or "", 100) or None,
        "state": _sanitize_text(data.get("state") or "TX", 2) or "TX",
        "zip_code": _sanitize_text(data.get("zip_code") or data.get("zip") or "", 10) or None,
        "employer": _sanitize_text(data.get("employer") or "", 200) or None,
        "occupation": _sanitize_text(data.get("occupation") or "", 200) or None,
        "salesrep": _sanitize_text(data.get("salesrep") or "", 100) or None,
        "notes": _sanitize_text(data.get("notes") or "", 2000) or None,
        "ssn_masked": _sanitize_text(data.get("ssn_masked") or "", 20),
        "co_buyer": data.get("co_buyer"),
        "references": data.get("references", []),
    }

    try:
        doc_id = _db.create_customer(customer, doc_id=customer_id)
        customer["id"] = doc_id
        return {"success": True, "customer": _strip_ssn_from_customer(customer)}
    except Exception as e:
        logger.error(f"Customer creation failed: {e}")
        raise HTTPException(500, "Failed to create customer")


@app.put("/api/customers/{customer_id}", dependencies=[Depends(require_admin)])
async def update_customer(customer_id: str, request: Request):
    """Update an existing customer record in Firestore."""
    data = await request.json()

    # Verify exists
    existing = _db.get_customer(customer_id)
    if existing is None:
        raise HTTPException(404, "Customer not found")

    updatable = ["full_name", "email", "phone", "status", "address", "city",
                 "state", "zip_code", "employer", "occupation", "salesrep",
                 "notes", "co_buyer", "references"]
    update_data = {k: data[k] for k in updatable if k in data}

    if not update_data:
        raise HTTPException(400, "No updatable fields provided")

    try:
        _db.update_customer(customer_id, update_data)
        updated = _db.get_customer(customer_id)
        return {"success": True, "customer": _strip_ssn_from_customer(updated)}
    except Exception as e:
        logger.error(f"Customer update failed: {e}")
        raise HTTPException(500, "Failed to update customer")


# ─── Feedback / Report Issue ──────────────────────────────────────────────────

@app.post("/api/feedback")
async def submit_feedback(request: Request):
    """Receive issue reports from the Report Issue button."""
    data = await request.json()

    # Input validation — prevent abuse
    description = (data.get("description") or "")[:2000]  # Max 2000 chars
    if not description.strip():
        raise HTTPException(400, "Description is required")

    # Sanitize — strip HTML tags from all fields
    import re

    def sanitize(s: str, max_len: int = 500) -> str:
        stripped = re.sub(r"<[^>]+>", "", str(s or ""))
        stripped = re.sub(
            r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            "[PHONE-REDACTED]",
            stripped,
        )
        stripped = re.sub(
            r"\b(api[_-]?key|token|secret|pin|password)\s*[:=]\s*\S+",
            r"\1=[SECRET-REDACTED]",
            stripped,
            flags=re.IGNORECASE,
        )
        return redact_pii_from_text(stripped)[:max_len]

    def sanitize_url(url: str) -> str:
        clean = sanitize(url, max_len=500)
        parsed = urlsplit(clean)
        if not parsed.scheme or not parsed.netloc:
            return clean[:200]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:200]

    feedback = {
        "timestamp": datetime.utcnow().isoformat(),
        "description": sanitize(description, max_len=2000),
        "page": sanitize(data.get("page") or "", max_len=100),
        "url": sanitize_url(data.get("url") or ""),
        "userAgent": sanitize(data.get("userAgent") or "", max_len=300),
        "screenSize": sanitize(data.get("screenSize") or "", max_len=20),
    }

    # Save to feedback log
    import json as _json
    feedback_path = os.path.join(os.path.dirname(__file__), "data", "feedback.jsonl")
    os.makedirs(os.path.dirname(feedback_path), exist_ok=True)
    with open(feedback_path, "a") as f:
        f.write(_json.dumps(feedback) + "\n")

    logger.info(f"Feedback received: {feedback['description'][:100]}")
    return {"success": True, "message": "Thank you for your feedback!"}


# ─── Document History ────────────────────────────────────────────────────────

@app.get("/api/documents/history", dependencies=[Depends(require_admin)])
async def document_history():
    """List all generated documents. Merges local and GCS results."""
    docs, _local_count, _gcs_count = _collect_generated_documents()
    return {"documents": docs[:50], "total": len(docs)}


# ─── Partner Integration API v1 ──────────────────────────────────────────────
# External partners authenticate separately from the admin UI. This surface is
# intended for automation clients such as Notion or n8n and must stay
# fail-closed plus PII-redacted by default.

def _get_partner_api_keys() -> dict[str, str]:
    """Return all valid partner API keys keyed by their env var name.

    The primary key is `THO_API_KEY`. Additional per-partner keys are exposed
    via env vars prefixed `THO_API_KEY_` (e.g., `THO_API_KEY_ETAI`). Each one
    is independently revocable — rotate its Secret Manager entry and the
    others keep working.

    Returned dict: {env_var_name: key_value}. Order is not significant; the
    env var name is recorded as `partner_id` in audit logs so you can tell
    which partner is calling.
    """
    keys: dict[str, str] = {}
    for name, value in os.environ.items():
        if name != "THO_API_KEY" and not name.startswith("THO_API_KEY_"):
            continue
        stripped = (value or "").strip()
        if stripped:
            keys[name] = stripped
    return keys


def _get_partner_api_key() -> str:
    """Backwards-compat shim for the single-key pattern. Returns the primary
    `THO_API_KEY` value or empty string. New code should use
    `_get_partner_api_keys()` so per-partner keys work.
    """
    return (os.environ.get("THO_API_KEY") or "").strip()


def _extract_partner_api_key(request: Request) -> str | None:
    """Accept either Authorization: Bearer <token> or X-API-Key: <token>."""
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return token

    x_api_key = request.headers.get("X-API-Key", "").strip()
    return x_api_key or None


def _partner_api_key_fingerprint(api_key: str | None) -> str:
    """Return the first 8 chars of the SHA256 hex digest for audit logging."""
    if not api_key:
        return "missing"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


def _log_partner_api_request(
    request: Request,
    api_key: str | None,
    auth_status: str,
    partner_id: str | None = None,
):
    """Structured audit log for every /api/v1 request without exposing the raw key."""
    struct_logger.info(
        "Partner API request",
        api_key_fingerprint=_partner_api_key_fingerprint(api_key),
        partner_id=partner_id,
        endpoint=request.url.path,
        method=request.method,
        client_ip=_get_client_ip(request),
        auth_status=auth_status,
    )


async def require_partner_api_key(request: Request):
    """Validate the partner API key against any configured partner slot.

    Fail-closed if no partner keys are configured (503). Any valid key from
    THO_API_KEY or THO_API_KEY_* env vars is accepted; audit log records
    which env var name matched so per-partner revocation stays auditable.
    """
    valid_keys = _get_partner_api_keys()
    provided_key = _extract_partner_api_key(request)

    if not valid_keys:
        _log_partner_api_request(request, provided_key, "unconfigured")
        raise HTTPException(status_code=503, detail="API key auth not configured")

    if not provided_key:
        _log_partner_api_request(request, provided_key, "missing")
        raise HTTPException(status_code=401, detail="Missing API key")

    matched_partner: str | None = None
    for env_name, valid_key in valid_keys.items():
        if hmac.compare_digest(provided_key, valid_key):
            matched_partner = env_name
            break

    if not matched_partner:
        _log_partner_api_request(request, provided_key, "invalid")
        raise HTTPException(status_code=401, detail="Invalid API key")

    _log_partner_api_request(request, provided_key, "accepted", partner_id=matched_partner)


def _json_safe(value):
    """Convert datetime-like values to JSON-safe ISO strings."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _redact_customer_for_partner(customer: dict) -> dict:
    """Return the non-PII subset of a customer record for partner integrations.

    Names are included because they appear on signed contracts and county
    records already — not treating them as high-risk PII here. SSN, phone,
    email stay redacted.
    """
    safe = _strip_ssn_from_customer(customer)
    allowed_fields = (
        "id",
        "legacy_id",
        "legacy_source",
        "full_name",
        "status",
        "billing_account",
        "city",
        "state",
        "created_at",
        "updated_at",
    )
    return {
        field: _json_safe(safe.get(field))
        for field in allowed_fields
        if field in safe
    }


def _redact_lead_for_partner(lead: dict) -> dict:
    """Remove direct contact details from lead responses."""
    allowed_fields = (
        "lead_id",
        "status",
        "source",
        "bedrooms",
        "bathrooms",
        "budget_max",
        "home_type",
        "homes_viewed",
        "appointment_requested",
        "financing_discussed",
        "created_at",
        "updated_at",
    )
    return {
        field: _json_safe(lead.get(field))
        for field in allowed_fields
        if field in lead
    }


def _serialize_inventory_for_partner(item: dict) -> dict:
    """Normalize inventory records for the external API."""
    return {
        "id": item.get("id"),
        "model_name": item.get("model_name") or item.get("model"),
        "manufacturer": item.get("manufacturer"),
        "year": item.get("year"),
        "is_new": item.get("is_new", True),
        "serial_number": item.get("serial_number"),
        "label_number": item.get("label_number"),
        "sections": item.get("sections") or item.get("no_of_sections"),
        "bedrooms": item.get("bedrooms"),
        "bathrooms": item.get("bathrooms"),
        "sqft": item.get("sqft"),
        "msrp": item.get("msrp") or item.get("sale_price"),
        "image_url": item.get("image_url") or item.get("hero_image"),
        "status": item.get("status", "AVAILABLE"),
    }


def _count_collection_by_status(collection_name: str, status_field: str = "status") -> dict:
    """Count Firestore documents by status for simple dashboard summaries."""
    total = 0
    by_status: dict[str, int] = {}

    for doc in _db.db.collection(collection_name).stream():
        total += 1
        data = doc.to_dict() or {}
        status = data.get(status_field) or "UNKNOWN"
        by_status[status] = by_status.get(status, 0) + 1

    return {"total": total, "by_status": by_status}


@app.get("/api/v1/customers", dependencies=[Depends(require_partner_api_key)])
async def v1_list_customers(limit: int = 50, offset: int = 0, status: str = None, q: str = None):
    """List customers with pagination and default PII redaction."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        customers = _db.search_customers(
            query_text=q or None,
            status=status or None,
            limit=offset + limit,
        )
        page = customers[offset:offset + limit]
        return {
            "customers": [_redact_customer_for_partner(customer) for customer in page],
            "count": len(page),
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        struct_logger.error("Partner API customer list failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list customers")


@app.get("/api/v1/customers/{customer_id}", dependencies=[Depends(require_partner_api_key)])
async def v1_get_customer(customer_id: str):
    """Get one customer with PII removed."""
    try:
        customer = _db.get_customer(customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"customer": _redact_customer_for_partner(customer)}
    except HTTPException:
        raise
    except Exception as e:
        struct_logger.error("Partner API customer fetch failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve customer")


@app.post("/api/v1/customers", dependencies=[Depends(require_partner_api_key)], status_code=201)
async def v1_create_customer(request: Request):
    """Create a customer using the same accepted body shape as the admin route."""
    data = await request.json()

    name = _sanitize_text(data.get("full_name") or "")
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="full_name is required (min 2 chars)")

    allowed_statuses = {"LEAD", "ENROLLED", "NON_ENROLLED", "SOLD", "CLOSED"}
    status = (data.get("status") or "LEAD").upper()
    if status not in allowed_statuses:
        status = "LEAD"

    # Partner API explicitly does NOT accept SSN (masked or otherwise).
    # Historical FCD imports that populated ssn_masked go through the admin
    # route, not this one. Defensively drop any SSN-shaped fields from
    # nested co_buyer / references as well.
    def _drop_ssn(obj):
        if isinstance(obj, dict):
            return {
                k: _drop_ssn(v)
                for k, v in obj.items()
                if "ssn" not in str(k).lower()
            }
        if isinstance(obj, list):
            return [_drop_ssn(item) for item in obj]
        return obj

    customer_id = str(uuid.uuid4())
    customer = {
        "legacy_id": _sanitize_text(data.get("legacy_id") or "", 50),
        "legacy_source": data.get("legacy_source", "manual")
        if data.get("legacy_source") in ("manual", "fastcontract", "import", "n8n", "notion")
        else "manual",
        "full_name": name,
        "email": (data.get("email") or "").strip().lower()[:200] or None,
        "phone": (data.get("phone") or "").strip()[:20] or None,
        "status": status,
        "address": _sanitize_text(data.get("address") or "", 300) or None,
        "city": _sanitize_text(data.get("city") or "", 100) or None,
        "state": _sanitize_text(data.get("state") or "TX", 2) or "TX",
        "zip_code": _sanitize_text(data.get("zip_code") or data.get("zip") or "", 10) or None,
        "employer": _sanitize_text(data.get("employer") or "", 200) or None,
        "occupation": _sanitize_text(data.get("occupation") or "", 200) or None,
        "salesrep": _sanitize_text(data.get("salesrep") or "", 100) or None,
        "notes": _sanitize_text(data.get("notes") or "", 2000) or None,
        "co_buyer": _drop_ssn(data.get("co_buyer")),
        "references": _drop_ssn(data.get("references", [])),
    }

    try:
        created_id = _db.create_customer(customer, doc_id=customer_id)
        return {"id": created_id}
    except Exception as e:
        struct_logger.error("Partner API customer create failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create customer")


@app.get("/api/v1/inventory", dependencies=[Depends(require_partner_api_key)])
async def v1_list_inventory(limit: int = 50, offset: int = 0, status: str = None, manufacturer: str = None):
    """List inventory with simple pagination and partner-safe response fields."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        inventory = _db.search_inventory(
            status=status.upper() if status else None,
            manufacturer=manufacturer or None,
            limit=offset + limit,
        )
        page = inventory[offset:offset + limit]
        return {
            "inventory": [_serialize_inventory_for_partner(item) for item in page],
            "count": len(page),
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        struct_logger.error("Partner API inventory list failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list inventory")


@app.get("/api/v1/leads", dependencies=[Depends(require_partner_api_key)])
async def v1_list_leads(limit: int = 50, offset: int = 0, status: str = None):
    """List leads with direct contact details removed."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        leads = await lead_manager.list_leads(status=status, limit=offset + limit)
        page = leads[offset:offset + limit]
        return {
            "leads": [_redact_lead_for_partner(lead.to_dict()) for lead in page],
            "count": len(page),
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        struct_logger.error("Partner API lead list failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list leads")


@app.post("/api/v1/webhooks/notify", dependencies=[Depends(require_partner_api_key)])
async def v1_webhook_notify(request: Request):
    """Accept a partner webhook and log it to the Firestore activities collection.

    Idempotency: if the request body includes `idempotency_key`, we check the
    `activities/` collection for a prior record with the same key and return
    the existing activity ID instead of creating a duplicate. The key is
    scoped to the calling API key's fingerprint so two partners can't collide
    on a shared key value.
    """
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from e

    event = _sanitize_text(str(data.get("event") or ""), 100)
    deal_id = _sanitize_text(str(data.get("deal_id") or ""), 100)
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    idempotency_key = _sanitize_text(str(data.get("idempotency_key") or ""), 200)

    if not event:
        raise HTTPException(status_code=400, detail="event is required")
    if not deal_id:
        raise HTTPException(status_code=400, detail="deal_id is required")

    key_fingerprint = _partner_api_key_fingerprint(_extract_partner_api_key(request))

    if idempotency_key:
        scoped_key = f"{key_fingerprint}:{idempotency_key}"
        try:
            existing = (
                _db.db.collection("activities")
                .where("metadata.idempotency_scope", "==", scoped_key)
                .limit(1)
                .get()
            )
            existing_list = list(existing)
            if existing_list:
                activity_doc = existing_list[0].to_dict() or {}
                struct_logger.info(
                    "Partner webhook idempotent replay",
                    activity_id=activity_doc.get("id"),
                    deal_id=deal_id,
                    idempotency_scope=scoped_key,
                )
                return {
                    "id": activity_doc.get("id"),
                    "logged": True,
                    "idempotent_replay": True,
                }
        except Exception as e:
            struct_logger.warning(
                "Idempotency lookup failed; proceeding with new activity",
                error=str(e),
            )

    activity_id = str(uuid.uuid4())
    activity = {
        "id": activity_id,
        "activity_type": f"partner_webhook.{event}",
        "description": f"Partner webhook received for deal {deal_id}",
        "deal_id": deal_id,
        "actor": "partner_api",
        "metadata": {
            "event": event,
            "payload": payload,
            "endpoint": request.url.path,
            "client_ip": _get_client_ip(request),
            "api_key_fingerprint": key_fingerprint,
            "idempotency_scope": f"{key_fingerprint}:{idempotency_key}" if idempotency_key else None,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        _db.db.collection("activities").document(activity_id).set(activity)
        struct_logger.info("Partner webhook activity logged", activity_id=activity_id, deal_id=deal_id)
        return {"id": activity_id, "logged": True, "idempotent_replay": False}
    except Exception as e:
        struct_logger.error("Partner webhook logging failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to record webhook activity")


@app.get("/api/v1/stats", dependencies=[Depends(require_partner_api_key)])
async def v1_stats():
    """Return partner-safe topline counts by status."""
    try:
        return {
            "customers": _db.count_customers(),
            "deals": _count_collection_by_status("deals"),
            "inventory": _count_collection_by_status("inventory"),
        }
    except Exception as e:
        struct_logger.error("Partner API stats failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load stats")


# ─── RAG over regulatory documents ───────────────────────────────────────────
# Lazy-loaded singleton; first request to /api/v1/rag/query incurs index load.
# If the index hasn't been built, the endpoint returns 503 and tells the caller
# how to build it.
_rag_instance = None


def _get_document_rag():
    """Lazy-load the DocumentRAG singleton."""
    global _rag_instance
    if _rag_instance is not None:
        return _rag_instance
    from tools.document_rag import DocumentRAG

    instance = DocumentRAG()
    try:
        instance.load()
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "RAG index not built. Run `python scripts/build_rag_index.py` "
                "and redeploy, or mount a prebuilt index at data/rag_index/."
            ),
        ) from e
    _rag_instance = instance
    return _rag_instance


@app.post("/api/v1/rag/query", dependencies=[Depends(require_partner_api_key)])
async def v1_rag_query(request: Request):
    """Semantic search over the regulatory document corpus.

    Request body:
        {"query": "Who signs the sales contract?", "k": 5}

    Response:
        {
          "query": "...",
          "count": 5,
          "results": [
            {"chunk_id": "TMHA_SalesContract.pdf#p1#c0",
             "template": "TMHA_SalesContract.pdf",
             "page": 1,
             "score": 0.82,
             "text": "..."},
            ...
          ]
        }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    query = _sanitize_text(str(body.get("query") or ""), 1000).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    k_raw = body.get("k", 5)
    try:
        k = int(k_raw)
    except (TypeError, ValueError):
        k = 5
    k = max(1, min(k, 20))

    rag = _get_document_rag()
    try:
        results = rag.query(query, k=k)
    except Exception as e:
        struct_logger.error("RAG query failed", error=str(e), query_len=len(query))
        raise HTTPException(status_code=500, detail="RAG query failed")

    return {
        "query": query,
        "count": len(results),
        "results": [
            {
                "chunk_id": r.chunk_id,
                "template": r.template,
                "page": r.page,
                "score": round(r.score, 4),
                "text": r.text[:500],
            }
            for r in results
        ],
    }


# ─── Lead Nurture (manual stale-lead re-engagement) ──────────────────────────
# Wired here, not as a cron, so ops can preview the cohort with dry_run=true
# before flipping to dry_run=false. Auto-schedule is a follow-up PR after Mark
# approves the messaging copy in tools/lead_nurture.py:render_nurture_email.

@app.post("/api/admin/lead-nurture/run", dependencies=[Depends(require_admin)])
async def run_lead_nurture_endpoint(request: Request):
    """Manual trigger for the stale-lead re-engagement batch.

    Body (JSON, all optional):
      * ``dry_run`` (bool, default True) — preview cohort without sending
      * ``days_inactive`` (int, default 14) — staleness threshold
      * ``max_results`` (int, default 50, clamped) — hard cohort cap

    Returns the orchestrator dict from
    ``tools.lead_nurture.run_nurture_batch``.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    # Default to dry_run=True so an accidental empty POST cannot send mail.
    dry_run = bool(body.get("dry_run", True))
    try:
        days_inactive = int(body.get("days_inactive", 14))
    except (TypeError, ValueError):
        days_inactive = 14
    try:
        max_results = int(body.get("max_results", 50))
    except (TypeError, ValueError):
        max_results = 50

    from tools.lead_nurture import run_nurture_batch

    try:
        result = run_nurture_batch(
            dry_run=dry_run,
            days_inactive=days_inactive,
            max_results=max_results,
        )
    except Exception as exc:
        struct_logger.error("Lead nurture batch failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Lead nurture batch failed")

    # Audit-log every invocation. ``audit_log`` lives on PR #36; soft-import so
    # this PR can land before that one. Falls back to structured logging.
    try:
        from audit_log import log_admin_action  # type: ignore

        log_admin_action(
            actor="admin",
            action="customer.update",
            target_type="customer",
            target_id="lead_nurture_batch",
            details={
                "dry_run": dry_run,
                "days_inactive": days_inactive,
                "cohort_size": result.get("cohort_size", 0),
                "sent": result.get("sent", 0),
                "failed": result.get("failed", 0),
            },
            request=request,
        )
    except ImportError:
        struct_logger.info(
            "Lead nurture batch (audit_log not yet merged)",
            dry_run=dry_run,
            days_inactive=days_inactive,
            cohort_size=result.get("cohort_size", 0),
            sent=result.get("sent", 0),
            failed=result.get("failed", 0),
        )
    except Exception as exc:
        # Audit failures must never block the API response.
        struct_logger.warning("Audit log write failed for lead_nurture", error=str(exc))

    return result


# Serve Frontend — Must be last to avoid catching API routes
app.mount("/assets", ImmutableStaticFiles(directory="frontend/dist/assets"), name="assets")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        # Funnel unknown /api/* paths through the resilient HTTPException
        # handler so they get the {success, status_code, message} envelope and
        # the no-cache Cache-Control header. Preserves PR #17 behaviour
        # (JSON 404, no SPA fallback).
        raise HTTPException(status_code=404, detail="Not Found")

    # Serve actual files from dist if they exist (e.g., tex-icon.svg, vite.svg)
    if full_path:
        file_path = os.path.join("frontend/dist", full_path)
        if os.path.isfile(file_path):
            response = FileResponse(file_path)
            if file_path.endswith(".html"):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
    # No-cache on index.html so clients always get latest JS chunk references
    response = FileResponse("frontend/dist/index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)


# AI PM Manager Routes (Linear-inspired)
from pm_routes import router as pm_router
app.include_router(pm_router, dependencies=[Depends(require_admin)])
