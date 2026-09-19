Warning: truncated output (original token count: 84679)
Total output lines: 8553

"""
FastAPI Application — Config-Driven AI Agent Server

Serves the AI agent backend and static frontend.
All business-specific config is loaded from config.yaml.
Admin routes use `X-Admin-Token` or `Authorization: Bearer <token>`;
partner integrations live under `/api/v1/*` and authenticate with `THO_API_KEY`.
"""
# ruff: noqa: E402

import os

# Configure Vertex AI before importing any ADK modules
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

import asyncio
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from urllib.parse import urlsplit, urlunsplit

import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

import caching
from auth.csrf import create_csrf_token, require_request_csrf, set_csrf_cookie
from auth.email_code import (
    EMAIL_CODE_TTL_SECONDS,
    MAX_CODE_ATTEMPTS,
    EmailLoginCodeStoreUnavailable,
    default_code_store,
    generate_code,
    hash_code,
)
from auth.google_ads_step_up_routes import router as google_ads_step_up_router
from auth.routes import is_allowed_admin_email
from auth.routes import router as passkey_router
from auth.session import SESSION_COOKIE_NAME as PASSKEY_COOKIE_NAME
from auth.session import SessionManager, validate_cloud_run_session_secret
from config_loader import business_name, get_deployment_config
from google_ads_admin.approval_routes import router as google_ads_approval_router
from inventory_classification import normalize_inventory_classification

# render_prompt is the SAME loader root_agent.py feeds the ADK runner — the
# /readyz probe renders the runner's prompts through it so a stripped-prompt
# image (the #223 chat outage) fails readiness instead of booting green.
from prompt_loader import render_prompt

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
        project_id = os.environ.get(
            "GOOGLE_CLOUD_PROJECT", deploy_cfg.get("project_id", "tho-ai-agent")
        )
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
            from google.adk.apps import App
            from google.adk.runners import InMemoryRunner

            from root_agent import root_agent

            _root_agent = root_agent
            _adk_app = App(name="root_agent", root_agent=_root_agent)
            _runner = InMemoryRunner(app=_adk_app)
            logger.info("ADK Runner initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize ADK runner: {e}")
            raise RuntimeError("AI services not available. Please try again later.")
    return _runner


import tools.feature_flags as feature_flags
from appointment_manager import Appointment, AppointmentManager
from audit_log import (
    ALLOWED_ACTIONS as AUDIT_ALLOWED_ACTIONS,
)
from audit_log import (
    ALLOWED_TARGET_TYPES as AUDIT_ALLOWED_TARGET_TYPES,
)
from audit_log import (
    log_admin_action,
    query_audit_log,
)
from chat_history import ChatHistory
from conversation_memory import ConversationMemory
from docuseal_service import (
    maybe_trigger_automated_signing as docuseal_auto_trigger,
)
from docuseal_service import (
    send_file_for_signature as docuseal_send_file_for_signature,
)
from docuseal_service import (
    send_for_signature as docuseal_send_for_signature,
)
from email_service import (
    get_email_log,
    notify_new_appointment,
    notify_new_lead,
    send_admin_login_code,
    send_appointment_confirmation,
    send_custom_email,
    send_deal_status_update,
    send_document_email,
    send_lead_welcome,
)
from lead_management import Lead, LeadManager
from structured_logging import logger as struct_logger
from tools.contact_capture import (
    apply_utm,
    capture_contact_from_message,
    capture_explicit_contact,
)
from tools.input_sanitizer import sanitize_body, sanitize_query_params
from tools.pii_guard import redact_pii_from_text, validate_no_pii_in_text
from tools.user_activity_log import log_user_action, query_user_activity


def _safe_audit(action: str, details: dict) -> None:
    """Wrap audit_log to never raise into the request hot path."""
    try:
        log_admin_action(
            actor="system",  # Background email actions are system-actor
            action=action,
            target_type="document",
            details=details,
            request=None,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            struct_logger.warning("Audit log write failed", action=action, error=str(exc))
        except Exception:
            pass


def _maybe_email_document(
    *,
    customer_email,
    customer_name,
    doc_filename: str,
    doc_type: str,
    download_url: str,
    deal_id=None,
    audit_action: str = "document.email_delivery",
) -> None:
    """Best-effort document delivery email.

    Silently skips when the customer email is missing. Catches and warn-logs
    every failure so the surrounding doc-generation request never breaks
    because email is flaky.
    """
    if not customer_email:
        try:
            struct_logger.info(
                "Document email skipped — no customer email",
                doc_filename=doc_filename,
                deal_id=deal_id,
            )
        except Exception:
            pass
        return

    try:
        result = send_document_email(
            to=customer_email,
            customer_name=customer_name or "",
            doc_filename=doc_filename,
            doc_type=doc_type,
            download_url=download_url,
            deal_id=deal_id,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            struct_logger.warning(
                "Document email send raised",
                error=str(exc),
                doc_filename=doc_filename,
                deal_id=deal_id,
            )
        except Exception:
            pass
        return

    _safe_audit(
        audit_action,
        {
            "to": customer_email,
            "doc_filename": doc_filename,
            "doc_type": doc_type,
            "deal_id": deal_id,
            "delivery": "ok" if result.get("success") else "failed",
            "error": result.get("error"),
        },
    )

    if not result.get("success"):
        try:
            struct_logger.warning(
                "Document email send returned non-success",
                error=result.get("error"),
                doc_filename=doc_filename,
                deal_id=deal_id,
            )
        except Exception:
            pass


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
APP_STARTED_AT = time.monotonic()

# Sentry error tracking — opt-in; no-op when SENTRY_DSN is absent or under pytest.
if os.environ.get("SENTRY_DSN") and not os.environ.get("PYTEST_CURRENT_TEST"):
    import re as _sentry_re

    import sentry_sdk

    _SENTRY_SSN_RE = _sentry_re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")
    _SENTRY_EMAIL_RE = _sentry_re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

    def _sentry_before_send(event, hint):
        # Drop health-probe events — noisy and quota-wasting.
        url = (event.get("request") or {}).get("url", "")
        if "/healthz" in url or url.rstrip("/").endswith("/health"):
            return None
        # Scrub PII from request body (mirrors tools/pii_guard.py patterns).
        body = (event.get("request") or {}).get("data", "")
        if isinstance(body, str):
            body = _SENTRY_SSN_RE.sub("[SSN-REDACTED]", body)
            body = _SENTRY_EMAIL_RE.sub("[EMAIL-REDACTED]", body)
            event.setdefault("request", {})["data"] = body
        return event

    def _sentry_traces_sampler(ctx):
        # Exclude health probes from performance tracing.
        path = (ctx.get("asgi_scope") or {}).get("path", "")
        if path.startswith("/health"):
            return 0
        # Conservative 0.05 default; tunable via env without a redeploy (e.g.
        # raise during an incident, set 0.0 to disable). Clamped to [0,1]; a
        # malformed value falls back to the default. Only reached when
        # SENTRY_DSN is set, so the no-op-when-unconfigured contract holds.
        try:
            rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
        except (TypeError, ValueError):
            rate = 0.05
        return min(max(rate, 0.0), 1.0)

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=os.environ.get("K_REVISION", "local"),
        release=os.environ.get("APP_VERSION", "local"),
        traces_sampler=_sentry_traces_sampler,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        before_send=_sentry_before_send,
    )
    logger.info("Sentry initialized (environment=%s)", os.environ.get("K_REVISION", "local"))

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
LLMS_TXT_PATH = os.path.join(os.path.dirname(__file__), "llms.txt")

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
        # CSP: allow self, inline styles (Tailwind), Google Fonts, Matterport
        # iframes, CloudFront CDN images. Vendor analytics/pixel script+connect
        # hosts are appended ONLY for IDs that are validly set (see
        # seo_routes.analytics_csp_sources, same _clean_id gate as the snippet);
        # with none set this header is byte-identical to the static baseline.
        import seo_routes

        _csp = [
            ("default-src", ["'self'"]),
            ("script-src", ["'self'", "'unsafe-inline'"]),
            ("style-src", ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"]),
            ("img-src", ["'self'", "https://d132mt2yijm03y.cloudfront.net", "https:", "data:"]),
            ("frame-src", ["https://my.matterport.com"]),
            ("connect-src", ["'self'"]),
            ("font-src", ["'self'", "https://fonts.gstatic.com", "data:"]),
            ("frame-ancestors", ["'none'"]),
        ]
        _extra = seo_routes.analytics_csp_sources()
        if _extra:
            for _name, _hosts in _csp:
                for _h in _extra.get(_name, []):
                    if _h not in _hosts:
                        _hosts.append(_h)
        response.headers["Content-Security-Policy"] = "; ".join(
            f"{_name} {' '.join(_hosts)}" for _name, _hosts in _csp
        )
        # HSTS: enforce HTTPS for 1 year (only effective on HTTPS connections)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def _get_client_ip(request: Request) -> str:
    """Get real client IP, checking X-Forwarded-For for reverse proxy (Cloud Run).

    Reused as the slowapi ``key_func`` so per-IP rate limiting and the
    Redis-backed brute-force counter key the same client identity.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# slowapi per-IP rate limiter — layered on top of the legacy
# RateLimitMiddleware below. Per-route caps are applied via @limiter.limit()
# decorators on individual endpoints (admin, partner /api/v1/*, marketing
# inventory-context). /health, /healthz, /healthz/ are exempted via
# @limiter.exempt so Cloud Run liveness probes are never throttled.
#
# headers_enabled is intentionally left FALSE: slowapi's _inject_headers
# raises when a route returns a dict (the common FastAPI shape) without an
# explicit ``response: Response`` parameter in the signature. The custom
# 429 handler below adds Retry-After by hand using the exception's limit
# metadata so callers still get the standard rate-limit signal.
limiter = Limiter(
    key_func=_get_client_ip,
    default_limits=["100/minute"],
    headers_enabled=False,
    # Fail-open hardening. With slowapi's default in-process memory storage
    # (no RATELIMIT_STORAGE_URI set) these are a no-op and limiting behaves
    # exactly as before. They only matter if an operator later points slowapi
    # at an external backend (e.g. Redis): in_memory_fallback_enabled re-checks
    # against in-memory storage if that backend is unreachable, and
    # swallow_errors lets the request through rather than 500 if even that path
    # fails — a rate-limit backend outage can never take a public route down.
    swallow_errors=True,
    in_memory_fallback_enabled=True,
)
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """JSON 429 handler with Retry-After. Replaces slowapi's default handler
    so we don't depend on ``headers_enabled`` (see comment above)."""
    try:
        retry_after = int(exc.limit.limit.get_expiry())
    except Exception:
        retry_after = 60
    return JSONResponse(
        {"error": f"Rate limit exceeded: {exc.detail}"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


# ── Per-route rate-limit values, operator-tunable via env ──────────────────
# slowapi accepts a Callable[..., str] as a limit value and evaluates it per
# request, so these caps can be tuned in Cloud Run with --update-env-vars and
# ZERO redeploy. When the env var is ABSENT the callable returns the
# conservative default below — identical to the previously-hardcoded value
# (strict no-op). A blank OR malformed override (anything slowapi's parser
# rejects, e.g. "10/sec") also falls back to the default, so a fat-fingered env
# var can never silently weaken or disable the per-route cap.
def _route_rate_limit(env_var: str, default: str):
    def _resolve() -> str:
        value = os.environ.get(env_var, "").strip()
        if not value:
            return default
        try:
            from limits import parse_many

            parse_many(value)  # validate it parses as a rate string
        except Exception:
            return default
        return value

    return _resolve


# Conservative defaults: a real human submits the contact form / books an
# appointment once. 10/min/IP leaves headroom for retries, shared NAT, and
# double-clicks while throttling an email-flood bot on the fresh Resend domain.
CONTACT_RATE_LIMIT = _route_rate_limit("CONTACT_RATE_LIMIT", "10/minute")
APPOINTMENTS_RATE_LIMIT = _route_rate_limit("APPOINTMENTS_RATE_LIMIT", "10/minute")

# Rate limiting middleware — per-IP sliding window
MAX_REQUESTS_PER_MINUTE = int(os.environ.get("RATE_LIMIT_RPM", "60"))
MAX_REQUEST_BODY_BYTES = int(
    os.environ.get("MAX_REQUEST_BODY_BYTES", str(1 * 1024 * 1024))
)  # 1 MB default


_STATIC_RATE_LIMIT_EXTENSIONS = (
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".map",
    ".png",
    ".svg",
    ".webmanifest",
    ".webp",
    ".woff",
    ".woff2",
)
_STATIC_RATE_LIMIT_PATHS = {
    "/manifest.webmanifest",
    "/registerSW.js",
    "/sw.js",
    "/tex-icon.svg",
    "/vite.svg",
}


def _is_rate_limit_exempt_path(path: str, method: str = "GET") -> bool:
    """Skip the legacy global limiter for health checks and SPA delivery."""
    if path in {"/health", "/healthz", "/healthz/", "/readyz", "/readyz/"}:
        return True
    if method.upper() in {"GET", "HEAD"} and not (path == "/api" or path.startswith("/api/")):
        return True
    if path.startswith("/assets/") or path.startswith("/workbox-"):
        return True
    if path in _STATIC_RATE_LIMIT_PATHS:
        return True
    if not (path == "/api" or path.startswith("/api/")):
        return path.lower().endswith(_STATIC_RATE_LIMIT_EXTENSIONS)
    return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if _is_rate_limit_exempt_path(request.url.path, request.method):
            return await call_next(request)
        client_ip = _get_client_ip(request)
        now = time.time()
        window = self._hits[client_ip]
        # Prune entries older than 60s
        self._hits[client_ip] = window = [t for t in window if now - t < 60]
        if len(window) >= MAX_REQUESTS_PER_MINUTE:
            return JSONResponse(
                {"error": "Rate limit exceeded. Please try again shortly."}, status_code=429
            )
        window.append(now)
        return await call_next(request)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse({"error": "Request body too large."}, status_code=413)
        return await call_next(request)


class InputSanitizationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if (
            request.method in {"POST", "PUT", "PATCH"}
            and request.url.path.startswith("/api/")
            and not request.url.path.startswith("/api/v1/")
        ):
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                try:
                    body = await request.body()
                    if body:
                        data = json.loads(body)
                        sanitized = sanitize_body(data)
                        request._body = json.dumps(sanitized).encode("utf-8")
                except Exception:
                    pass  # Not valid JSON or sanitization failed — leave body untouched
        # Sanitize query params for all API routes so endpoints can trust request.state.sanitized_query
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v1/"):
            raw = dict(request.query_params)
            request.state.sanitized_query = sanitize_query_params(raw)
        return await call_next(request)


class _MetricsStore:
    """Thread-safe rolling-window store for request latency metrics."""

    def __init__(self):
        self._global_buffer = deque(maxlen=5000)
        self._endpoint_buffers = defaultdict(lambda: deque(maxlen=1000))
        self._lock = threading.Lock()

    def record(self, endpoint_key: str, duration_ms: float, status_code: int) -> None:
        with self._lock:
            self._global_buffer.append((duration_ms, status_code))
            self._endpoint_buffers[endpoint_key].append((duration_ms, status_code))

    @staticmethod
    def _calculate_percentiles(durations: list[float]) -> dict:
        if not durations:
            return {"p50": None, "p95": None, "p99": None}
        sorted_durations = sorted(durations)
        n = len(sorted_durations)

        def _p(p: float) -> float:
            k = (n - 1) * p / 100.0
            f = int(k)
            c = min(f + 1, n - 1)
            if f == c:
                return sorted_durations[f]
            return sorted_durations[f] * (c - k) + sorted_durations[c] * (k - f)

        return {"p50": round(_p(50), 2), "p95": round(_p(95), 2), "p99": round(_p(99), 2)}

    def get_metrics(self) -> dict:
        with self._lock:
            global_data = list(self._global_buffer)
            endpoint_data = {key: list(buf) for key, buf in self._endpoint_buffers.items()}

        global_durations = [d for d, _ in global_data]
        overall = self._calculate_percentiles(global_durations)
        overall["count"] = len(global_data)

        endpoints = {}
        for key, records in endpoint_data.items():
            durations = [d for d, _ in records]
            status_codes = [s for _, s in records]
            error_count = sum(1 for s in status_codes if s >= 400)
            endpoints[key] = {
                **self._calculate_percentiles(durations),
                "count": len(records),
                "error_rate": round(error_count / len(records), 4) if records else 0.0,
            }

        return {"overall": overall, "endpoints": endpoints}


_metrics_store = _MetricsStore()


class PerformanceMetricsMiddleware(BaseHTTPMiddleware):
    """Track request latency and record structured metrics per endpoint."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/healthz", "/healthz/", "/readyz", "/readyz/"}:
            return await call_next(request)

        start = time.perf_counter()
        status_code = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            endpoint_key = f"{request.method} {request.url.path}"
            _metrics_store.record(endpoint_key, duration_ms, status_code)
            struct_logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
                timestamp=datetime.now(UTC).isoformat(),
            )


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(InputSanitizationMiddleware)
# slowapi middleware is registered last so it sits outermost and runs
# before the legacy per-IP RateLimitMiddleware. Per-route caps via
# @limiter.limit decorators short-circuit hot paths (e.g. /api/admin/verify
# at 5/min) before they ever reach the brute-force _pin_attempts counter.
app.add_middleware(SlowAPIMiddleware)


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
#       staff-backed reads revalidate; archived legacy reads may cache for 1h
#   * Any other /api/* path: no-cache (CRM data must never be cached)
#   * Non-/api paths: header is left untouched so the SPA / static asset
#     handlers can set their own caching policy.
#
# This is additive on top of PR #17 ("Return JSON 404 for unknown API paths"):
# the SPA catch-all now raises HTTPException(404) for unknown /api/* paths so
# they flow through this single envelope rather than emitting a bare detail.

_PUBLIC_INVENTORY_CACHE = "max-age=3600, public, stale-while-revalidate=60"
_DYNAMIC_API_CACHE = "no-cache"


def _public_inventory_cache_policy() -> str:
    # A browser/CDN must not hide staff changes for an hour after a save.
    return _PUBLIC_INVENTORY_CACHE if _inventory_source_pref() == "legacy" else _DYNAMIC_API_CACHE


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
        response.headers["Cache-Control"] = _public_inventory_cache_policy()
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
            response.headers["Cache-Control"] = _public_inventory_cache_policy()
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
async def resilient_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
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
_default_origins = [
    "https://tho-agent-691674245427.us-central1.run.app",
    "https://tho-agent-trgi34bxuq-uc.a.run.app",
    "https://tho-ai-agent.web.app",
    "https://tho-ai-agent.firebaseapp.com",
    "https://tho.sapphirealpha.xyz",
    "https://sapphirealpha.xyz",
    "https://www.sapphirealpha.xyz",
    "https://texashomeoutlet.com",
    "https://www.texashomeoutlet.com",
]
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", ",".join(_default_origins)).split(",")
    if o.strip()
]
if IS_LOCAL:
    ALLOWED_ORIGINS += ["http://localhost:8080", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "Accept", "X-Admin-Token", "Authorization"],
)


CANONICAL_PUBLIC_URL = os.environ.get(
    "CANONICAL_PUBLIC_URL", "https://www.texashomeoutlet.com"
).rstrip("/")
_CANONICAL_PUBLIC_PARTS = urlsplit(CANONICAL_PUBLIC_URL)
_CANONICAL_PUBLIC_SCHEME = _CANONICAL_PUBLIC_PARTS.scheme or "https"
_CANONICAL_PUBLIC_HOST = _CANONICAL_PUBLIC_PARTS.netloc or "www.texashomeoutlet.com"


def _should_redirect_to_canonical_host(request: Request) -> bool:
    """Keep operator/client navigation on the production vanity domain.

    Cloud Run's default *.run.app URL remains useful for probes and low-level
    diagnostics, but customer-facing pages must settle on the dealership's
    public domain so search authority and support links share one origin.
    """
    if request.method.upper() not in {"GET", "HEAD"}:
        return False

    host = request.headers.get("host", "")
    host_name = host.split(":", 1)[0].lower().rstrip(".")
    if not host_name.endswith(".run.app"):
        return False

    path = request.url.path
    if path in {"/llms.txt", "/robots.txt", "/sitemap.xml"}:
        return False
    if (
        path.startswith("/health")
        or path.startswith("/readyz")
        or path == "/api"
        or path.startswith("/api/")
    ):
        return False
    if path.startswith("/assets/") or path.startswith("/workbox-"):
        return False
    if path in _STATIC_RATE_LIMIT_PATHS:
        return False
    return True


class CanonicalHostMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _should_redirect_to_canonical_host(request):
            target = urlunsplit(
                (
                    _CANONICAL_PUBLIC_SCHEME,
                    _CANONICAL_PUBLIC_HOST,
                    request.url.path,
                    request.url.query,
                    "",
                )
            )
            return RedirectResponse(target, status_code=308)
        return await call_next(request)


app.add_middleware(CanonicalHostMiddleware)
app.add_middleware(PerformanceMetricsMiddleware)
# Outermost layer: gzip the fully-formed response. minimum_size skips tiny
# payloads (redirects, JSON acks) where compression overhead isn't worth it;
# the server-rendered SEO HTML + JSON-LD shrink ~70%, a real TTFB/bandwidth win.
app.add_middleware(GZipMiddleware, minimum_size=500)


# ─── Admin Auth Setup ───

# Admin PIN hash — MUST be set via ADMIN_PIN_HASH env var in production.
# Generate hash: python -c "import hashlib; print(hashlib.sha256(b'YOUR_PIN').hexdigest())"
# Cloud Run and local app startup fail closed; tests and operators must set
# ADMIN_PIN_HASH explicitly.
_CONFIGURED_ADMIN_PIN_HASH = os.environ.get("ADMIN_PIN_HASH")
if os.environ.get("K_SERVICE"):
    ADMIN_PIN_HASH = _CONFIGURED_ADMIN_PIN_HASH or ""
    if not ADMIN_PIN_HASH:
        raise RuntimeError("ADMIN_PIN_HASH is mandatory in Cloud Run")
else:
    ADMIN_PIN_HASH = _CONFIGURED_ADMIN_PIN_HASH or ""
    if not ADMIN_PIN_HASH:
        raise RuntimeError("Set ADMIN_PIN_HASH env var to run locally")

# Warn loudly if email service is not configured (appointments/leads won't get confirmations)
if not os.environ.get("RESEND_API_KEY") and not IS_LOCAL:
    logger.critical(
        "RESEND_API_KEY not set — appointment confirmations and lead emails will NOT be sent."
    )
elif not os.environ.get("RESEND_API_KEY"):
    logger.warning("RESEND_API_KEY not set — emails will run in dry-run mode (local dev).")

# JWT-based admin tokens — works across multiple Cloud Run instances.
# Uses HMAC-SHA256 with the independent session secret shared across instances.
import base64
import hmac
import struct

# Production session claims must use an independent secret that no shared PIN
# holder can derive. Local development keeps the stable fallback to avoid
# invalidating sessions on every restart.
if os.environ.get("K_SERVICE"):
    validate_cloud_run_session_secret(
        os.environ.get("ADMIN_SESSION_SECRET"),
        admin_pin_hash=ADMIN_PIN_HASH,
    )
elif not os.environ.get("ADMIN_SESSION_SECRET"):
    if ADMIN_PIN_HASH:
        _derived_secret = hashlib.sha256(f"tho-session-v2-{ADMIN_PIN_HASH}".encode()).hexdigest()
        os.environ["ADMIN_SESSION_SECRET"] = _derived_secret
        logger.info("ADMIN_SESSION_SECRET derived from PIN hash for local stability")

ADMIN_TOKEN_TTL = int(os.environ.get("ADMIN_TOKEN_TTL", str(24 * 60 * 60)))  # 24 hours
# Separate PIN tokens from passkey cookies and bind the entire verifier so
# either secret rotation or PIN rotation revokes existing PIN sessions.
# No legacy-key fallback: older PIN cookies must sign in again after promotion.
_JWT_SECRET = hmac.new(
    os.environ["ADMIN_SESSION_SECRET"].encode("utf-8"),
    f"tho-pin-session-v2:{ADMIN_PIN_HASH}".encode(),
    hashlib.sha256,
).digest()


def _create_admin_token() -> str:
    """Create an HMAC-signed JWT-like token with embedded expiration."""
    if not ADMIN_PIN_HASH:
        raise RuntimeError("Admin auth not configured")
    expires = int(time.time()) + ADMIN_TOKEN_TTL
    payload = struct.pack(">Q", expires)  # 8 bytes, big-endian uint64
    sig = hmac.new(_JWT_SECRET, payload, hashlib.sha256).digest()[:16]  # 16-byte signature
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


_passkey_session_manager: SessionManager | None = None


def _get_passkey_session_manager() -> SessionManager:
    global _passkey_session_manager
    if _passkey_session_manager is None:
        _passkey_session_manager = SessionManager()
    return _passkey_session_manager


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


def _verify_passkey_cookie(request: Request) -> bool:
    """Check the passkey session cookie."""
    token = request.cookies.get(PASSKEY_COOKIE_NAME, "")
    if not token:
        return False
    mgr = _get_passkey_session_manager()
    payload = mgr.verify_session(token)
    return payload is not None and payload.get("user_id") == "admin"


def _admin_token_from_request(request: Request) -> str:
    """Read an admin token from cookie or supported auth headers."""
    # Prefer httpOnly cookie (post-hardening)
    token = request.cookies.get("tho_admin_token", "").strip()
    if token:
        return token

    # Fallback to headers for backward compatibility
    token = request.headers.get("X-Admin-Token", "").strip()
    if token:
        return token

    authorization = request.headers.get("Authorization", "").strip()
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return ""


def _extract_utm(data: dict) -> dict:
    """Pull first-party UTM/referrer from a lead-submit payload. Length-capped,
    never PII. Honest attribution: only set when the visitor reached out."""

    def clip(v):
        return (str(v).strip()[:200] or None) if v else None

    def click_id(v):
        value = clip(v)
        if value and re.fullmatch(r"[A-Za-z0-9._~-]{6,200}", value):
            return value
        return None

    return {
        "utm_source": clip(data.get("utm_source")),
        "utm_medium": clip(data.get("utm_medium")),
        "utm_campaign": clip(data.get("utm_campaign")),
        "utm_content": clip(data.get("utm_content")),
        "utm_term": clip(data.get("utm_term")),
        "referrer": clip(data.get("referrer")),
        "gclid": click_id(data.get("gclid")),
        "gbraid": click_id(data.get("gbraid")),
        "wbraid": click_id(data.get("wbraid")),
    }


_JOURNEY_ID_RE = re.compile(r"^j_[0-9a-f]{32}$")
_ANALYTICS_EVENT_ALIASES = {
    "tour_click": "tour_opened",
}
_ANALYTICS_ALLOWED_PROPS = {
    "page_viewed": {"page", "page_path", "path"},
    "home_view": {"home", "home_id", "status", "page_path", "path"},
    "home_viewed": {"home", "home_id", "status", "page_path", "path"},
    "lead_form_opened": {"home", "home_id", "type", "source", "page_path", "path"},
    "tour_opened": {"home", "home_id", "page_path", "path"},
    "photo_clicked": {"home", "home_id", "index", "page_path", "path"},
    "inventory_show_more": {"shown", "visible", "total", "remaining", "page_path", "path"},
    "appointment_handoff_started": {
        "source",
        "home",
        "home_id",
        "intent",
        "page_path",
        "path",
    },
    "appointment_handoff_completed": {
        "source",
        "home",
        "home_id",
        "intent",
        "page_path",
        "path",
    },
    "phone_clicked": {"page_path", "placement", "path"},
    "lead_captured": {"source", "type", "home", "home_id", "intent", "path"},
    "appointment_booked": {"source", "home", "home_id", "intent", "path"},
    "review_redirect": {"src"},
}
_SERVER_ONLY_ANALYTICS_EVENTS = {"lead_captured", "appointment_booked"}


def _normalize_journey_id(value) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if _JOURNEY_ID_RE.fullmatch(candidate) else None


def _extract_attribution(data: dict) -> dict:
    return {
        **_extract_utm(data),
        "journey_id": _normalize_journey_id(data.get("journey_id")),
    }


def _canonical_analytics_event(value, *, public: bool = False) -> str | None:
    event = str(value or "").strip()[:64]
    event = _ANALYTICS_EVENT_ALIASES.get(event, event)
    if event not in _ANALYTICS_ALLOWED_PROPS:
        return None
    if public and event in _SERVER_ONLY_ANALYTICS_EVENTS:
        return None
    return event


def _store_analytics_event(
    event,
    props: dict | None = None,
    *,
    journey_id=None,
    public: bool = False,
) -> bool:
    """Write one privacy-bounded analytics record.

    Public callers may emit engagement only; conversion events are accepted
    from server-side durable-write paths. Unknown properties, PII-shaped fields,
    raw IPs, and malformed journey identifiers never enter Firestore.
    """
    canonical = _canonical_analytics_event(event, public=public)
    if not canonical:
        return False
    allowed = _ANALYTICS_ALLOWED_PROPS[canonical]
    clean_props = {
        str(key): str(value)[:200]
        for key, value in (props or {}).items()
        if key in allowed and value is not None and str(value).strip()
    }
    record = {
        "event": canonical,
        "schema_version": 2,
        "props": clean_props,
        "created_at": datetime.now(UTC).isoformat(),
    }
    normalized_journey = _normalize_journey_id(journey_id)
    if normalized_journey:
        record["journey_id"] = normalized_journey
    try:
        if _db and getattr(_db, "db", None):
            _db.db.collection("analytics_events").add(record, timeout=FIRESTORE_RPC_TIMEOUT)
        return True
    except Exception as exc:
        struct_logger.warning("Analytics event store failed", event=canonical, error=str(exc))
        return False


async def require_admin(request: Request):
    """FastAPI dependency that validates the stateless admin token or passkey session."""
    if _verify_passkey_cookie(request):
        require_request_csrf(request)
        return
    token = _admin_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    if not _verify_admin_token(token):
        raise HTTPException(
            status_code=401, detail="Admin session expired. Please re-authenticate."
        )
    require_request_csrf(request)


@app.get("/api/metrics", dependencies=[Depends(require_admin)])
async def get_metrics():
    """Return rolling-window performance metrics for admin review."""
    return _metrics_store.get_metrics()


def _audit_actor(request: Request) -> str:
    """Derive a short stable actor id from the admin token.

    We only have a single shared PIN today, but the JWT payload's expiration
    differs per login, so two operators on the same PIN get distinct actor
    ids in the audit log. We hash + truncate the token so we never persist
    the raw bearer value.
    """
    try:
        token = _admin_token_from_request(request)
    except Exception:
        token = ""
    if not token:
        return "admin"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"admin:{digest}"


# Brute-force protection: track failed PIN attempts per IP
# Redis-backed with in-memory fallback for local dev without Redis
PIN_MAX_ATTEMPTS = 10
PIN_LOCKOUT_SECONDS = 300  # 5-minute lockout after 10 failures
_pin_attempts_fallback: dict[str, list[float]] = {}


def _pin_attempts_key(client_ip: str) -> str:
    return f"tho:pin_attempts:{client_ip}"


def _get_pin_attempts(client_ip: str) -> list[float]:
    """Retrieve recent failed PIN attempts for a client IP."""
    redis_client = caching.get_redis_client()
    if redis_client:
        try:
            data = redis_client.get(_pin_attempts_key(client_ip))
            if data:
                attempts = json.loads(data)
                now = time.time()
                attempts = [t for t in attempts if now - t < PIN_LOCKOUT_SECONDS]
                return attempts
        except Exception as e:
            struct_logger.warning("Redis pin_attempts read failed", error=str(e))
    # Fallback to in-memory
    attempts = _pin_attempts_fallback.get(client_ip, [])
    now = time.time()
    attempts = [t for t in attempts if now - t < PIN_LOCKOUT_SECONDS]
    return attempts


def _add_pin_attempt(client_ip: str, timestamp: float) -> None:
    """Record a failed PIN attempt for a client IP."""
    attempts = _get_pin_attempts(client_ip)
    attempts.append(timestamp)
    redis_client = caching.get_redis_client()
    if redis_client:
        try:
            redis_client.setex(
                _pin_attempts_key(client_ip),
                PIN_LOCKOUT_SECONDS,
                json.dumps(attempts),
            )
            return
        except Exception as e:
            struct_logger.warning("Redis pin_attempts write failed", error=str(e))
    _pin_attempts_fallback[client_ip] = attempts


def _clear_pin_attempts(client_ip: str) -> None:
    """Clear failed PIN attempts after successful login."""
    redis_client = caching.get_redis_client()
    if redis_client:
        try:
            redis_client.delete(_pin_attempts_key(client_ip))
        except Exception:
            pass
    _pin_attempts_fallback.pop(client_ip, None)


AI_RUN_TIMEOUT = float(os.environ.get("AI_RUN_TIMEOUT", "45.0"))  # seconds


async def _execute_agent_run(runner, user_id, session_id, new_message, request_id):
    """Run the ADK agent and collect the response text."""
    result_generator = runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
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

        struct_logger.info(
            f"Event {event_count}",
            request_id=request_id,
            event_type=event_type,
            content_role=content_role,
            has_content=has_content,
            content_preview=content_text,
        )

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

    return final_text, event_count


# --- Rate Limiting for Chat API ---
CHAT_RATE_LIMIT_SECONDS = 60
CHAT_RATE_LIMIT_MAX_REQUESTS = 10
_chat_rate_limit_fallback: dict[str, list[float]] = {}


def _check_chat_rate_limit(client_ip: str) -> bool:
    """Return True if the request is allowed, False if rate limited."""
    now = time.time()
    redis_client = caching.get_redis_client()
    key = f"tho:chat_ratelimit:{client_ip}"

    if redis_client:
        try:
            # Simple fixed window or rolling window
            data = redis_client.get(key)
            attempts = json.loads(data) if data else []
            attempts = [t for t in attempts if now - t < CHAT_RATE_LIMIT_SECONDS]
            if len(attempts) >= CHAT_RATE_LIMIT_MAX_REQUESTS:
                return False
            attempts.append(now)
            redis_client.setex(key, CHAT_RATE_LIMIT_SECONDS, json.dumps(attempts))
            return True
        except Exception as e:
            struct_logger.warning("Redis chat_ratelimit failed", error=str(e))

    # Fallback to in-memory
    attempts = _chat_rate_limit_fallback.get(client_ip, [])
    attempts = [t for t in attempts if now - t < CHAT_RATE_LIMIT_SECONDS]
    if len(attempts) >= CHAT_RATE_LIMIT_MAX_REQUESTS:
        return False
    attempts.append(now)
    _chat_rate_limit_fallback[client_ip] = attempts
    return True


@app.post("/run")
async def run_agent(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_chat_rate_limit(client_ip):
        return JSONResponse(
            {"error": "You're sending messages too fast. Please wait a moment and try again."},
            status_code=429,
        )

    request_id = str(uuid.uuid4())
    start_time = time.time()

    try:
        data = await request.json()
        user_id = data.get("userId", "default_user")
        session_id = data.get("sessionId") or f"anon_{uuid.uuid4().hex[:12]}"
        new_message_dict = data.get("newMessage")
        # First-party UTM/referrer the frontend carries on the chat POST, so a
        # chat-sourced lead is attributable to the paid campaign that drove it
        # (mirrors /api/contact). Non-PII, length-capped.
        utm = _extract_attribution(data)

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
            struct_logger.warning(
                "PII detected in user message",
                request_id=request_id,
                findings=pii_check["findings"],
            )

        # Get conversation context
        context = None
        try:
            context = await conversation_memory.get_context(session_id, user_id)
            context_prompt = context.preferences.to_prompt_context()
            struct_logger.info(
                "Context retrieved", request_id=request_id, has_preferences=bool(context_prompt)
            )
        except Exception as e:
            struct_logger.warning("Context retrieval failed", request_id=request_id, error=str(e))

        # Create ADK Content object
        from google.genai import types

        new_message = types.Content(role="user", parts=[types.Part(text=text_content)])

        # Get runner (lazy initialization)
        try:
            runner = _get_runner()
        except RuntimeError as e:
            struct_logger.error("AI service unavailable", request_id=request_id, error=str(e))
            return {"error": "AI service temporarily unavailable. Please try again later."}

        # Ensure session exists
        existing_session = await runner.session_service.get_session(
            app_name="root_agent", user_id=user_id, session_id=session_id
        )

        if not existing_session:
            struct_logger.info("Session created", request_id=request_id, session_id=session_id)
            await runner.session_service.create_session(
                app_name="root_agent", user_id=user_id, session_id=session_id
            )

        # Run agent with timeout
        try:
            final_text, _event_count = await asyncio.wait_for(
                _execute_agent_run(runner, user_id, session_id, new_message, request_id),
                timeout=AI_RUN_TIMEOUT,
            )
        except TimeoutError:
            struct_logger.warning("Agent run timed out", request_id=request_id)
            final_text = (
                "I apologize, but the request timed out. "
                "Please try again with a shorter message."
            )

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
                search_results=None,
            )
            struct_logger.info("Context updated", request_id=request_id)

            # Auto-create/update lead from conversation
            try:
                existing_lead = await lead_manager.get_lead_by_session(session_id)
                if existing_lead and context:
                    existing_lead.bedrooms = context.preferences.bedrooms or existing_lead.bedrooms
                    existing_lead.bathrooms = (
                        context.preferences.bathrooms or existing_lead.bathrooms
                    )
                    existing_lead.budget_max = (
                        context.preferences.max_budget or existing_lead.budget_max
                    )
                    existing_lead.homes_viewed = context.homes_discussed
                    existing_lead.appointment_requested = context.appointment_intent
                    existing_lead.financing_discussed = context.financing_questions > 0
                    # Backfill first-touch UTM onto a RETURNING visitor's existing
                    # lead: localStorage reuses their session_id, so this branch (not
                    # the create branch) runs — without it a returning paid click on
                    # an already-known chat lead is under-attributed (Codex #254 P2).
                    apply_utm(existing_lead, utm)
                    await lead_manager.update_lead(existing_lead)
                elif context and (
                    context.preferences.bedrooms
                    or context.preferences.max_budget
                    or context.homes_discussed
                ):
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
                        source="chat",
                        **utm,
                    )
                    await lead_manager.create_lead(new_lead)
            except Exception as e:
                struct_logger.warning("Lead management failed", request_id=request_id, error=str(e))

        except Exception as e:
            struct_logger.warning("Context update failed", request_id=request_id, error=str(e))

        # Passive contact-capture backstop: if the visitor typed a phone/email in
        # THIS message, make sure it becomes an actionable, staff-alerted lead —
        # independent of whether the model remembered to call the save_lead tool.
        # It dedupes BY PHONE against any already-captured lead (e.g. one save_lead
        # persisted this same turn), so it fires exactly once per contact — but
        # still fires when save_lead was called yet FAILED to persist, so a
        # high-intent lead is never silently lost.
        try:
            await capture_contact_from_message(
                text_content,
                session_id,
                user_id,
                lead_manager=lead_manager,
                notify=notify_new_lead,
                utm=utm,
            )
        except Exception as e:
            struct_logger.warning(
                "Contact-capture backstop failed", request_id=request_id, error=str(e)
            )

        duration_ms = (time.time() - start_time) * 1000
        struct_logger.response(request_id, len(final_text), duration_ms)

        log_user_action(
            action="chat.message",
            session_id=session_id,
            details={"request_id": request_id, "duration_ms": round(duration_ms, 1)},
            request=request,
        )

        return {"text": final_text}

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        import traceback

        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        struct_logger.error(
            "Chat request failed",
            request_id=request_id,
            error=error_detail,
            duration_ms=duration_ms,
            user_id=user_id,
            session_id=session_id,
        )

        # Friendly message for users, but specific for debugging
        user_message = (
            "I'm having trouble connecting to my brain right now. Please try again in a moment."
        )
        return {"error": user_message}


@app.get("/api/chat/session/{session_id}")
@limiter.limit("30/minute")
async def get_public_chat_session(session_id: str, request: Request):
    """Retrieve chat history for a given session ID to persist memory on the frontend.

    Rate-limited to blunt enumeration of the 48-bit anon session ids (customers
    sometimes paste PII into chat). Full per-browser session binding is tracked
    as a follow-up hardening item.
    """
    try:
        session = await chat_history.get_session(session_id)
        if not session:
            return {"success": True, "messages": []}

        messages = []
        for msg in session.messages:
            messages.append({"role": msg.role, "text": msg.text, "timestamp": msg.timestamp})

        return {"success": True, "messages": messages}
    except Exception as e:
        struct_logger.error(
            "Failed to retrieve public chat session", session_id=session_id, error=str(e)
        )
        return JSONResponse(
            {"success": False, "error": "Failed to retrieve chat history"}, status_code=500
        )


@app.get("/leads/export", dependencies=[Depends(require_admin)])
async def export_leads(status: str = None):
    """Export leads to CSV format."""
    try:
        leads = await lead_manager.list_leads(status=status, limit=1000)
        if not leads:
            return {"message": "No leads found", "count": 0}

        import csv
        import io

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
            headers={
                "Content-Disposition": f"attachment; filename=leads_{status or 'all'}_{int(time.time())}.csv"
            },
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
            "financing_discussed": 0,
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


def _categorize_lead_source(lead) -> str:
    """Map raw Lead.source (and any future utm/referrer fields) to a
    coarse category used for attribution charts.

    Buckets:
      - chat            : in-app conversational agent
      - contact_form    : website contact form
      - appointment     : appointment widget / direct booking
      - calculator      : financing calculator entry point
      - referrer:<host> : explicit referrer host (truncated)
      - utm:<source>    : utm_source value (lowercase)
      - other           : everything else
    """
    raw = (getattr(lead, "source", None) or "").strip().lower()

    # Future-proof: support utm_source / referrer fields if they ever
    # land on the Lead dataclass without crashing on AttributeError.
    utm = (getattr(lead, "utm_source", None) or "").strip().lower()
    referrer = (getattr(lead, "referrer", None) or "").strip().lower()

    if utm:
        return f"utm:{utm}"
    if referrer:
        # Strip protocol + path → keep host
        host = referrer.replace("https://", "").replace("http://", "").split("/", 1)[0][:40]
        return f"referrer:{host}" if host else "referrer:direct"

    if raw in {"chat", "appointment", "calculator", "contact_form", "chat_intake"}:
        # Normalize chat_intake → chat for funnel parity
        return "chat" if raw == "chat_intake" else raw
    if not raw:
        return "other"
    return raw[:40]


@app.get("/api/admin/crm/lead-sources", dependencies=[Depends(require_admin)])
async def admin_lead_sources(days: int = 30):
    """
    Lead source attribution for the last `days` days.

    Returns counts per category and a revenue-equivalent figure when
    deals can be attributed (matched on email or phone). Default window
    is 30 days; cap at 365.

    Response shape:
      {
        "success": true,
        "window_days": 30,
        "total_leads": N,
        "categories": [
          {"category": "chat", "count": N, "pct": 12.5,
           "attributed_deals": K, "attributed_revenue": $X}
        ]
      }
    """
    from caching import cache_get, cache_set

    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 365))
    cache_key = f"admin_lead_sources_{days}d_v1"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        from datetime import datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        cutoff_naive = cutoff.replace(tzinfo=None)

        all_leads = await lead_manager.list_leads(limit=2000)

        # Collect deals once, index by email/phone for attribution lookup
        deals_by_email: dict[str, list[dict]] = {}
        deals_by_phone: dict[str, list[dict]] = {}
        for doc in _db.db.collection("deals").stream(timeout=FIRESTORE_RPC_TIMEOUT):
            data = doc.to_dict() or {}
            status = (data.get("status") or "").lower()
            if status not in ("funded", "complete", "approved", "contract"):
                # Only count real revenue-equivalents. Skip pending/denied/archived.
                if status not in ("funded", "complete"):
                    continue
            email = (data.get("buyer_email") or "").strip().lower()
            phone_digits = "".join(c for c in (data.get("buyer_phone") or "") if c.isdigit())
            # Normalize to last 10 digits to match the lead-side comparison
            phone_digits = phone_digits[-10:] if len(phone_digits) >= 10 else phone_digits
            if email:
                deals_by_email.setdefault(email, []).append(data)
            if phone_digits:
                deals_by_phone.setdefault(phone_digits, []).append(data)

        category_counts: dict[str, int] = {}
        category_revenue: dict[str, float] = {}
        category_deals: dict[str, int] = {}
        seen_deal_ids: dict[str, set[str]] = {}

        recent_lead_count = 0
        for lead in all_leads:
            created = _parse_iso_datetime(getattr(lead, "created_at", None))
            if created and created < cutoff_naive:
                continue
            recent_lead_count += 1
            cat = _categorize_lead_source(lead)
            category_counts[cat] = category_counts.get(cat, 0) + 1

            # Attribution: match by email OR last-10-digits phone
            email = (getattr(lead, "email", None) or "").strip().lower()
            phone_digits = "".join(c for c in (getattr(lead, "phone", None) or "") if c.isdigit())
            phone_digits = phone_digits[-10:] if len(phone_digits) >= 10 else phone_digits

            matched_deals: list[dict] = []
            if email and email in deals_by_email:
                matched_deals.extend(deals_by_email[email])
            if phone_digits and phone_digits in deals_by_phone:
                matched_deals.extend(deals_by_phone[phone_digits])

            seen = seen_deal_ids.setdefault(cat, set())
            for deal in matched_deals:
                deal_id = deal.get("id") or deal.get("deal_id") or ""
                if not deal_id or deal_id in seen:
                    continue
                seen.add(deal_id)
                category_deals[cat] = category_deals.get(cat, 0) + 1
                # Use sale_price → loan_amount → home_price as revenue proxy
                revenue = (
                    deal.get("sale_price") or deal.get("loan_amount") or deal.get("home_price") or 0
                )
                try:
                    category_revenue[cat] = category_revenue.get(cat, 0.0) + float(revenue)
                except (TypeError, ValueError):
                    pass

        total = max(recent_lead_count, 1)
        categories = []
        for cat, count in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            categories.append(
                {
                    "category": cat,
                    "count": count,
                    "pct": round(100.0 * count / total, 1),
                    "attributed_deals": category_deals.get(cat, 0),
                    "attributed_revenue": round(category_revenue.get(cat, 0.0), 2),
                }
            )

        result = {
            "success": True,
            "window_days": days,
            "total_leads": recent_lead_count,
            "categories": categories,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        cache_set(cache_key, result, ttl_seconds=300)
        return result
    except Exception as e:
        struct_logger.error("Lead source attribution failed", error=str(e))
        return {"success": False, "error": "Failed to compute lead source attribution."}


@app.get("/api/analytics/leads", dependencies=[Depends(require_admin)])
async def get_lead_analytics(range: str = "30d"):
    """Get detailed lead analytics with time series data."""
    try:
        import builtins
        from datetime import datetime, timedelta

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
                return dt.astimezone(UTC).replace(tzinfo=None)
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
        dated_leads = [
            (lead, created_at) for lead in all_leads if (created_at := parse_created_at(lead))
        ]
        if range_key != "all":
            dated_leads = [
                (lead, created_at) for lead, created_at in dated_leads if created_at >= start_date
            ]
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
            "status_trends": {},
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
        date_range = (
            7
            if range_key == "7d"
            else 30
            if range_key == "30d"
            else 90
            if range_key == "90d"
            else min(30, len(filtered_leads) or 1)
        )

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


@app.get("/api/analytics/events", dependencies=[Depends(require_admin)])
async def get_event_analytics(range: str = "30d"):
    """Aggregate the client event stream (home views, quote/tour clicks, form
    opens) that POST /api/analytics captures into the `analytics_events`
    collection. This is the funnel signal that was previously written and never
    surfaced — which homes drive intent, what visitors do, day over day."""
    try:
        from collections import Counter
        from datetime import datetime, timedelta

        if range == "all":
            cutoff = ""  # empty string sorts before any ISO timestamp -> keep every event
        else:
            days = {"7d": 7, "30d": 30, "90d": 90}.get(range, 30)
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        events: list[dict] = []
        try:
            # ISO timestamps sort lexicographically = chronologically; read the
            # collection and keep in-range docs. (Low launch volume; move to a
            # created_at .where()+index if it grows.)
            for doc in (
                _db.db.collection("analytics_events")
                .limit(10000)
                .stream(timeout=FIRESTORE_RPC_TIMEOUT)
            ):
                d = doc.to_dict() or {}
                if str(d.get("created_at", "")) >= cutoff:
                    events.append(d)
        except Exception as e:
            struct_logger.warning("analytics_events read failed", error=str(e))

        discarded_events = 0
        canonical_events: list[dict] = []
        for event_record in events:
            canonical = _canonical_analytics_event(event_record.get("event"))
            if not canonical:
                discarded_events += 1
                continue
            canonical_events.append({**event_record, "event": canonical})

        by_type = Counter(e["event"] for e in canonical_events)
        top_homes = Counter(
            (e.get("props") or {}).get("home")
            for e in canonical_events
            if (e.get("props") or {}).get("home")
        )
        daily = Counter(str(e.get("created_at") or "")[:10] for e in canonical_events)
        attributed = [e for e in canonical_events if _normalize_journey_id(e.get("journey_id"))]
        journeys = {_normalize_journey_id(e.get("journey_id")) for e in attributed}
        lead_form_journeys = {
            _normalize_journey_id(e.get("journey_id"))
            for e in attributed
            if e["event"] == "lead_form_opened"
        }
        lead_journeys = {
            _normalize_journey_id(e.get("journey_id"))
            for e in attributed
            if e["event"] == "lead_captured"
        }
        appointment_journeys = {
            _normalize_journey_id(e.get("journey_id"))
            for e in attributed
            if e["event"] == "appointment_booked"
        }
        phone_journeys = {
            _normalize_journey_id(e.get("journey_id"))
            for e in attributed
            if e["event"] == "phone_clicked"
        }

        def percentage(numerator: int, denominator: int) -> float:
            return round(100 * numerator / denominator, 1) if denominator else 0.0

        return {
            "range": range,
            "total_events": len(canonical_events),
            "discarded_events": discarded_events,
            "by_type": [{"event": k, "count": v} for k, v in by_type.most_common()],
            "top_homes": [{"home": k, "count": v} for k, v in top_homes.most_common(10)],
            "daily_trend": [{"date": d, "events": c} for d, c in sorted(daily.items()) if d],
            "journey_funnel": {
                "eligible_journeys": len(journeys),
                "lead_form_journeys": len(lead_form_journeys),
                "lead_journeys": len(lead_journeys),
                "appointment_journeys": len(appointment_journeys),
                "phone_journeys": len(phone_journeys),
                "lead_conversion_rate": percentage(len(lead_journeys), len(journeys)),
                "appointment_conversion_rate": percentage(len(appointment_journeys), len(journeys)),
                "attribution_coverage_pct": percentage(len(attributed), len(canonical_events)),
            },
        }
    except Exception as e:
        struct_logger.error("Event analytics failed", error=str(e))
        return {"error": "Failed to load event analytics"}


@app.get("/api/analytics/documents", dependencies=[Depends(require_admin)])
async def get_document_analytics(range: str = "30d"):
    """Get document generation analytics."""
    try:
        import os
        from datetime import datetime

        # This would ideally come from a database
        # For now, we'll provide placeholder data structure
        output_dir = OUTPUT_DIR

        stats = {
            "total_generated": 0,
            "this_month": 0,
            "by_type": {},
            "pages_this_month": 0,
            "most_popular": {"name": "TMHA Sales Contract", "count": 0},
            "recent": [],
        }

        # Scan output directory for generated documents
        if os.path.exists(output_dir):
            all_files = []
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    if file.endswith(".pdf"):
                        filepath = os.path.join(root, file)
                        stat = os.stat(filepath)
                        all_files.append(
                            {
                                "name": file,
                                "created": datetime.fromtimestamp(stat.st_mtime),
                                "size": stat.st_size,
                            }
                        )

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
                    "created_at": f["created"].isoformat(),
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
            "new_with_photos": sum(
                1
                for h in inventory
                if h.get("is_new", True) and (h.get("image_url") or h.get("photos"))
            ),
            "used_with_photos": sum(
                1
                for h in inventory
                if not h.get("is_new", True) and (h.get("image_url") or h.get("photos"))
            ),
            "with_tours": sum(1 for h in inventory if h.get("matterport_id")),
            "top_viewed": sorted(inventory, key=lambda x: x.get("view_count", 0), reverse=True)[
                :10
            ],
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
            "conversion_to_lead": 0,
        }

        return stats
    except Exception as e:
        struct_logger.error("Chat analytics failed", error=str(e))
        return {"error": "Failed to load chat analytics"}


@app.get("/health")
@app.head("/health")
@limiter.exempt
def health():
    return {"status": "ok"}


@app.get("/llms.txt")
@app.head("/llms.txt")
@limiter.exempt
def llms_txt() -> FileResponse:
    return FileResponse(
        LLMS_TXT_PATH,
        media_type="text/plain; charset=utf-8",
        # noindex per Google guidance: llms.txt can otherwise leak into SERPs.
        headers={"Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex"},
    )


@app.get("/healthz", response_class=JSONResponse)
@app.head("/healthz", response_class=JSONResponse)
@app.get("/healthz/", response_class=JSONResponse)
@app.head("/healthz/", response_class=JSONResponse)
@limiter.exempt
def healthz() -> JSONResponse:
    """Public health probe — returns minimal data to avoid info leakage.

    Includes version (git SHA) for deploy verification. SHA is public info.
    """
    version = (
        os.environ.get("APP_VERSION")
        or os.environ.get("GIT_SHA")
        or os.environ.get("SOURCE_COMMIT")
        or "local"
    )
    return JSONResponse(
        {"status": "ok", "version": version},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


_EMAIL_LIVENESS_CACHE: dict[str, object] = {"checked_at": 0.0, "status": None}
_EMAIL_LIVENESS_TTL_S = 300.0
_EMAIL_LIVENESS_LOCK = threading.Lock()


def _cached_email_liveness() -> dict[str, object]:
    """Return a bounded, non-secret provider check cached for five minutes."""
    now = time.monotonic()
    cached = _EMAIL_LIVENESS_CACHE.get("status")
    checked_at = float(_EMAIL_LIVENESS_CACHE.get("checked_at") or 0.0)
    if isinstance(cached, dict) and now - checked_at < _EMAIL_LIVENESS_TTL_S:
        return cached
    with _EMAIL_LIVENESS_LOCK:
        cached = _EMAIL_LIVENESS_CACHE.get("status")
        checked_at = float(_EMAIL_LIVENESS_CACHE.get("checked_at") or 0.0)
        if isinstance(cached, dict) and now - checked_at < _EMAIL_LIVENESS_TTL_S:
            return cached
        try:
            from email_service import check_email_liveness

            status = check_email_liveness()
        except Exception:  # Detailed health must degrade, never fail or leak details.
            status = {"ok": False, "state": "unreachable"}
        _EMAIL_LIVENESS_CACHE.update(checked_at=time.monotonic(), status=status)
        return status


@app.get("/healthz/detailed", response_class=JSONResponse)
@limiter.exempt
def healthz_detailed(request: Request) -> JSONResponse:
    """Detailed diagnostics — requires admin auth (cookie, passkey, or token)."""
    if not _verify_passkey_cookie(request):
        token = _admin_token_from_request(request)
        if not token or not _verify_admin_token(token):
            raise HTTPException(status_code=403, detail="Admin access required")
    email_status = _cached_email_liveness()
    warnings = []
    if email_status["state"] == "not_configured":
        warnings.append("email_not_configured")
    elif email_status["state"] == "invalid_key":
        warnings.append("email_key_rejected")
    elif email_status["state"] == "unreachable":
        warnings.append("email_provider_unreachable")
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
            "email": email_status["state"],
        },
        "warnings": warnings,
    }
    return JSONResponse(
        body,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


# ─────────────────────── Readiness probe (/readyz) ──────────────────────────
#
# Distinct from /healthz (liveness). /healthz answers "is the process up?" and
# stays 200 even on a broken image — which is exactly how the 2026-06-23 chat
# outage (#223) hid: the container booted, /healthz was green, but a blanket
# `*.md` ignore rule had stripped prompts/*.md from the image, so the ADK runner
# could not init and every /run errored. /readyz exercises the RUNTIME deps a
# working request needs, so a broken image fails the probe.

# The ADK runner (root_agent.py) renders exactly these three prompts at init.
# Rendering them here through the SAME loader proves the prompt templates
# shipped in the image; a stripped-prompt image raises -> readiness fails.
_READYZ_RUNNER_PROMPTS = ("root_agent", "sales_agent", "service_agent")

# Regulatory PDF templates that MUST be present for document generation to work
# on Cloud Run (a deploy that strips tho_documents/ must fail readiness). Kept
# small + representative; the full manifest is enforced by
# tests/test_deploy_assets.py.
_READYZ_REQUIRED_DOCUMENTS = (
    "TMHA-TwoPartyContract.pdf",
    "Internal_ImportantNoticeTax.pdf",
)


def _readyz_documents_dir() -> str:
    """Directory holding the regulatory PDF templates (seam for tests)."""
    from tools.document_tools import DOCUMENTS_DIR

    return DOCUMENTS_DIR


def _readyz_check_prompts() -> dict:
    """Render the runner's prompt templates so a stripped-prompt image fails."""
    rendered = []
    for name in _READYZ_RUNNER_PROMPTS:
        text = render_prompt(name)
        if not text or not text.strip():
            raise RuntimeError(f"prompt '{name}' rendered empty")
        rendered.append(name)
    return {"ok": True, "rendered": rendered}


def _readyz_check_documents() -> dict:
    """Confirm the expected regulatory templates exist in the image."""
    docs_dir = _readyz_documents_dir()
    missing = [
        name
        for name in _READYZ_REQUIRED_DOCUMENTS
        if not os.path.isfile(os.path.join(docs_dir, name))
    ]
    if missing:
        raise RuntimeError(f"missing regulatory templates: {missing}")
    return {"ok": True, "present": list(_READYZ_REQUIRED_DOCUMENTS)}


def _readyz_check_firestore() -> dict:
    """Best-effort Firestore reachability. Raises on failure (soft-handled)."""
    from database.firestore_client import get_database

    # Touch the client; .db lazily constructs the Firestore handle. We do NOT
    # issue a query (would write/read prod) — constructing the client is enough
    # to surface a misconfigured/unreachable backend.
    client = get_database().db
    if client is None:
        raise RuntimeError("firestore client unavailable")
    return {"ok": True}


def _readyz_check_inventory_source() -> dict:
    """Report selected-source truth without making stale data a process outage."""
    requested = _inventory_source_pref()
    if requested == "legacy":
        context = load_legacy_inventory_snapshot_metadata()
        status = source_status(
            context,
            requested=requested,
            selected_path="legacy",
        )
        status["ok"] = status.get("freshness") == "fresh"
        status["current_inventory_count"] = context.get("total_inventory")
        return status

    context = _resolve_public_inventory_context()
    status = dict(context.get("source_status") or {})
    status["ok"] = status.get("freshness") == "fresh"
    status["current_inventory_count"] = context.get("current_inventory_count")
    return status


@app.api_route("/readyz", methods=["GET", "HEAD"], response_class=JSONResponse)
@app.api_route("/readyz/", methods=["GET", "HEAD"], response_class=JSONResponse)
@limiter.exempt
def readyz() -> JSONResponse:
    """Real readiness probe — exercises runtime deps a working request needs.

    Hard checks (failure -> 503): prompt templates render, regulatory documents
    present. Soft checks (reported, never fail the serving process): Firestore
    reachability and selected inventory-source freshness. Returns 200
    {"ready": true, "checks": {...}} or 503
    {"ready": false, "failed": "<check>", "checks": {...}}.

    This is the probe that would have caught the #223 chat outage: strip the
    prompts and /readyz goes 503 while /healthz stays 200.
    """
    checks: dict[str, dict] = {}
    failed: str | None = None

    # Hard checks — a failure means the deployed image cannot serve requests.
    for name, fn in (("prompts", _readyz_check_prompts), ("documents", _readyz_check_documents)):
        try:
            checks[name] = fn()
        except Exception as exc:  # noqa: BLE001 - probe must not raise into the handler
            # Message is operational (asset names / counts), never PII or secrets.
            checks[name] = {"ok": False, "error": str(exc)[:300]}
            if failed is None:
                failed = name

    # Soft check — DB blips should not flap Cloud Run readiness.
    try:
        checks["firestore"] = _readyz_check_firestore()
    except Exception as exc:  # noqa: BLE001
        checks["firestore"] = {"ok": False, "error": str(exc)[:300]}

    # Inventory freshness is a data-quality signal, not process liveness. Keep
    # serving the known catalog while making stale/unknown evidence explicit.
    try:
        checks["inventory"] = _readyz_check_inventory_source()
    except Exception as exc:  # noqa: BLE001
        checks["inventory"] = {"ok": False, "error": str(exc)[:300]}

    headers = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
    if failed is not None:
        return JSONResponse(
            {"ready": False, "failed": failed, "checks": checks},
            status_code=503,
            headers=headers,
        )
    return JSONResponse({"ready": True, "checks": checks}, headers=headers)


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
from schemas.document_schemas import (
    GenerateDocumentRequest,
    GeneratePacketRequest,
    SalesContractForm,
)
from tools.document_engine_v2 import (
    generate_batch as engine_generate_batch,
)
from tools.document_engine_v2 import (
    generate_document as engine_generate_document,
)
from tools.document_engine_v2 import (
    generate_packet as engine_generate_packet,
)
from tools.document_engine_v2 import (
    get_all_field_definitions as engine_get_all_field_definitions,
)
from tools.document_engine_v2 import (
    get_template_fields as engine_get_template_fields,
)
from tools.document_engine_v2 import (
    list_available_packets as engine_list_packets,
)
from tools.document_engine_v2 import (
    list_available_templates as engine_list_templates,
)
from tools.document_tools import (
    OUTPUT_DIR,
    download_from_gcs,
    generate_sales_contract_pdf,
    list_gcs_documents,
)

_SYNTHETIC_DOCUMENT_NAME_MARKERS = (
    "another_test",
    "_batch_",
    "burnin",
    "created_from_missing_dir",
    "doc_smoke",
    "document_ui_smoke",
    "e2e",
    "jane_doe",
    "joe_blo",
    "john_doe",
    "john_smith",
    "legacy_test",
    "prod_smoke",
    "browser_smoke",
    "playwright_smoke",
    "qa_browser",
    "quality_buyer",
    "smoke_buyer",
    "ui_smoke",
    "test_buyer",
    "testbuyer",
    "test_engine",
    "test_fill",
    "test_summary",
    "uiburnin",
)

_QUARANTINED_DOCUMENT_NAME_MARKERS = (
    # Generated before the May 15 quality gate and confirmed non-pristine by QA.
    "garett_t_floyd",
    # Legacy generated copies of this Note/Security template are not client-ready.
    "twopartycontract",
)

_DOCUMENT_HISTORY_DEFAULT_MIN_CREATED_AT = datetime(2026, 5, 15, 23, 14, tzinfo=UTC)


def _is_synthetic_document(filename: str | None) -> bool:
    """Identify generated test/smoke PDFs so admin history stays demo-safe."""
    normalized = os.path.basename(filename or "").lower()
    if not normalized.endswith(".pdf"):
        return False
    return any(marker in normalized for marker in _SYNTHETIC_DOCUMENT_NAME_MARKERS)


def _is_quarantined_generated_document(filename: str | None) -> bool:
    """Hide generated PDFs that QA has marked unsafe to present to reps."""
    normalized = os.path.basename(filename or "").lower()
    if not normalized.endswith(".pdf"):
        return False

    configured_markers = tuple(
        marker.strip().lower()
        for marker in os.environ.get("DOCUMENT_HISTORY_QUARANTINE_MARKERS", "").split(",")
        if marker.strip()
    )
    markers = _QUARANTINED_DOCUMENT_NAME_MARKERS + configured_markers
    return any(marker in normalized for marker in markers)


def _document_history_min_created_at() -> datetime:
    configured = os.environ.get("DOCUMENT_HISTORY_MIN_CREATED_AT", "").strip()
    if not configured:
        return _DOCUMENT_HISTORY_DEFAULT_MIN_CREATED_AT
    try:
        return datetime.fromisoformat(configured.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Invalid DOCUMENT_HISTORY_MIN_CREATED_AT; using default")
        return _DOCUMENT_HISTORY_DEFAULT_MIN_CREATED_AT


def _is_legacy_unverified_document(created_at: str | None) -> bool:
    if not created_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed < _document_history_min_created_at()


def _visible_generated_documents(docs: list[dict]) -> list[dict]:
    return [
        doc
        for doc in docs
        if not doc.get("synthetic")
        and not doc.get("quality_blocked")
        and not doc.get("legacy_unverified")
    ]


def _document_history_flags(filename: str | None, created_at: str | None) -> dict[str, bool]:
    return {
        "synthetic": _is_synthetic_document(filename),
        "quality_blocked": _is_quarantined_generated_document(filename),
        "legacy_unverified": _is_legacy_unverified_document(created_at),
    }


def _packet_result_fields(result: dict) -> dict:
    """Normalize legacy and v2 packet-engine response shapes for API callers."""
    merged = result.get("merged") if isinstance(result.get("merged"), dict) else {}
    if not merged and isinstance(result.get("merged_document"), dict):
        merged = result.get("merged_document") or {}

    filename = result.get("filename") or merged.get("filename")
    download_url = (
        result.get("download_url")
        or merged.get("download_url")
        or (f"/api/documents/download/{filename}" if filename else None)
    )
    page_count = result.get("page_count") or merged.get("page_count") or 0

    documents_included = result.get("documents_included")
    documents_skipped = result.get("documents_skipped")
    documents = result.get("documents") if isinstance(result.get("documents"), list) else []
    if documents_included is None and documents:
        documents_included = [
            doc.get("template_name") or doc.get("filename")
            for doc in documents
            if isinstance(doc, dict) and doc.get("success")
        ]
        documents_included = [doc for doc in documents_included if doc]
    if documents_skipped is None and documents:
        documents_skipped = [
            {
                "template": doc.get("template_name") or doc.get("filename") or "unknown",
                "reason": doc.get("error") or doc.get("message") or "Generation failed",
            }
            for doc in documents
            if isinstance(doc, dict) and not doc.get("success")
        ]

    message = result.get("message")
    if not message:
        count …34679 tokens truncated… if not lead:
            return {"success": False, "error": "Lead not found"}
        return {"success": True, "lead": lead.to_dict()}
    except Exception as e:
        struct_logger.error("Lead fetch failed", error=str(e))
        return {"success": False, "error": "Failed to load lead details. Please try again."}


@app.put("/api/leads/{lead_id}", dependencies=[Depends(require_admin)])
async def update_lead_api(lead_id: str, request: Request):
    """Update non-lifecycle CRM triage fields only.

    Contact data, lifecycle state, attribution, and system timestamps are not
    editable through this broad endpoint. Status uses the dedicated lifecycle
    transition so first-response timing cannot be forged or overwritten.
    """
    try:
        data = await request.json()
        allowed_fields = {"priority", "assigned_to", "triage_notes", "triage_reason"}
        if not isinstance(data, dict) or not data or not set(data).issubset(allowed_fields):
            raise HTTPException(
                status_code=400,
                detail="Only priority, assigned_to, triage_notes, and triage_reason are editable here.",
            )
        lead = await lead_manager.get_lead(lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        changed_fields: list[str] = []
        for key, value in data.items():
            if getattr(lead, key) != value:
                setattr(lead, key, value)
                changed_fields.append(key)
        if changed_fields:
            await lead_manager.update_lead(lead)
        log_admin_action(
            actor=_audit_actor(request),
            action="lead.update",
            target_type="lead",
            target_id=str(lead_id),
            details={"fields": sorted(changed_fields)},
            request=request,
        )
        return {"success": True, "message": "Lead updated", "lead": lead.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        struct_logger.error("Lead update failed", error=str(e))
        return {"success": False, "error": "Failed to update lead. Please try again."}


@app.patch("/api/leads/{lead_id}/lifecycle", dependencies=[Depends(require_admin)])
async def transition_lead_lifecycle_api(lead_id: str, request: Request):
    """Apply one explicit, validated, idempotent lead status transition."""
    try:
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {"status"}:
            raise HTTPException(status_code=400, detail="Payload must contain only status.")
        new_status = data.get("status")
        if not isinstance(new_status, str):
            raise HTTPException(status_code=400, detail="Status must be a string.")

        try:
            lead, previous_status, changed = await lead_manager.transition_lead_status(
                lead_id,
                new_status,
                actor=_audit_actor(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

        log_admin_action(
            actor=_audit_actor(request),
            action="lead.lifecycle_transition",
            target_type="lead",
            target_id=str(lead_id),
            details={
                "from_status": previous_status,
                "to_status": new_status,
                "changed": changed,
            },
            request=request,
        )
        return {"success": True, "changed": changed, "lead": lead.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        struct_logger.error("Lead lifecycle transition failed", error=str(e))
        return {"success": False, "error": "Failed to update lead status. Please try again."}


@app.get("/api/crm/appointments", dependencies=[Depends(require_admin)])
async def list_appointments_api(status: str = None, limit: int = 100):
    """List appointments for CRM dashboard."""
    try:
        appts = await appointment_manager.list_appointments(status=status, limit=limit)
        return {"success": True, "appointments": [a.to_dict() for a in appts], "count": len(appts)}
    except Exception as e:
        struct_logger.error("Appointment listing failed", error=str(e))
        return {"success": False, "error": "Failed to load appointments. Please try again."}


_crm_tasks: dict[str, dict] = {}
_CRM_TASK_COLLECTION = "crm_tasks"
_CRM_TASK_PRIORITIES = {"low", "medium", "high"}
_CRM_TASK_STATUSES = {"pending", "completed"}


def _crm_task_collection():
    try:
        db_client = getattr(_db, "db", None)
        if db_client is None:
            return None
        return db_client.collection(_CRM_TASK_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        struct_logger.warning("CRM task Firestore collection unavailable", error=str(exc))
        return None


def _serialize_crm_task(task: dict) -> dict:
    """Return the task shape the CRM frontend expects."""
    return dict(task)


def _load_crm_tasks_from_store() -> list[dict]:
    collection = _crm_task_collection()
    if collection is None:
        return list(_crm_tasks.values())
    try:
        tasks = []
        for doc in collection.limit(500).stream(timeout=FIRESTORE_RPC_TIMEOUT):
            data = doc.to_dict() or {}
            data.setdefault("task_id", doc.id)
            tasks.append(data)
        _crm_tasks.update({task["task_id"]: task for task in tasks if task.get("task_id")})
        return tasks
    except Exception as exc:  # noqa: BLE001
        struct_logger.warning("CRM task Firestore list failed", error=str(exc))
        return list(_crm_tasks.values())


def _save_crm_task(task: dict) -> None:
    _crm_tasks[task["task_id"]] = task
    collection = _crm_task_collection()
    if collection is None:
        return
    try:
        collection.document(task["task_id"]).set(task, timeout=FIRESTORE_RPC_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        struct_logger.warning("CRM task Firestore save failed", error=str(exc))


def _load_crm_task(task_id: str) -> dict | None:
    task = _crm_tasks.get(task_id)
    collection = _crm_task_collection()
    if collection is None:
        return task
    try:
        doc = collection.document(task_id).get(timeout=FIRESTORE_RPC_TIMEOUT)
        if doc.exists:
            data = doc.to_dict() or {}
            data.setdefault("task_id", doc.id)
            _crm_tasks[task_id] = data
            return data
    except Exception as exc:  # noqa: BLE001
        struct_logger.warning("CRM task Firestore fetch failed", error=str(exc))
    return task


@app.get("/api/crm/tasks", dependencies=[Depends(require_admin)])
async def list_crm_tasks(limit: int = 100, status: str | None = None):
    """List lightweight CRM follow-up tasks for the employee dashboard."""
    limit = max(1, min(limit, 200))
    tasks = _load_crm_tasks_from_store()
    if status:
        tasks = [task for task in tasks if task.get("status") == status]
    tasks.sort(key=lambda task: task.get("created_at", ""), reverse=True)
    return {
        "success": True,
        "tasks": [_serialize_crm_task(task) for task in tasks[:limit]],
        "count": min(len(tasks), limit),
        "total": len(tasks),
    }


@app.post("/api/crm/tasks", dependencies=[Depends(require_admin)])
async def create_crm_task(request: Request):
    """Create a CRM follow-up task from the employee dashboard."""
    try:
        data = await request.json()
    except (JSONDecodeError, ValueError):
        return JSONResponse({"success": False, "error": "Malformed JSON body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse(
            {"success": False, "error": "Request body must be a JSON object."},
            status_code=400,
        )

    title = str(data.get("title") or "").strip()
    if not title:
        return JSONResponse({"success": False, "error": "Task title is required."}, status_code=400)

    priority = str(data.get("priority") or "medium").strip().lower()
    if priority not in _CRM_TASK_PRIORITIES:
        priority = "medium"

    now = datetime.now(UTC).isoformat()
    task_id = str(uuid.uuid4())
    task = {
        "task_id": task_id,
        "title": title[:200],
        "description": str(data.get("description") or "").strip()[:2000],
        "due_date": str(data.get("due_date") or "").strip()[:40],
        "priority": priority,
        "assigned_to": str(data.get("assigned_to") or "").strip()[:120],
        "related_lead": str(data.get("related_lead") or "").strip()[:120],
        "related_deal": str(data.get("related_deal") or "").strip()[:120],
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    _save_crm_task(task)
    log_admin_action(
        actor=_audit_actor(request),
        action="crm_task.create",
        target_type="crm_task",
        target_id=task_id,
        details={
            "priority": priority,
            "related_lead": task["related_lead"] or None,
            "related_deal": task["related_deal"] or None,
        },
        request=request,
    )
    return {"success": True, "task": _serialize_crm_task(task)}


@app.put("/api/crm/tasks/{task_id}", dependencies=[Depends(require_admin)])
async def update_crm_task(task_id: str, request: Request):
    """Update a CRM task status or editable fields."""
    task = _load_crm_task(task_id)
    if not task:
        return JSONResponse({"success": False, "error": "Task not found."}, status_code=404)

    try:
        data = await request.json()
    except (JSONDecodeError, ValueError):
        return JSONResponse({"success": False, "error": "Malformed JSON body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse(
            {"success": False, "error": "Request body must be a JSON object."},
            status_code=400,
        )

    if "status" in data:
        status = str(data.get("status") or "").strip().lower()
        if status not in _CRM_TASK_STATUSES:
            return JSONResponse(
                {"success": False, "error": "status must be pending or completed."},
                status_code=400,
            )
        task["status"] = status
    for key, max_len in {
        "title": 200,
        "description": 2000,
        "due_date": 40,
        "assigned_to": 120,
        "related_lead": 120,
        "related_deal": 120,
    }.items():
        if key in data:
            task[key] = str(data.get(key) or "").strip()[:max_len]
    if "priority" in data:
        priority = str(data.get("priority") or "").strip().lower()
        if priority in _CRM_TASK_PRIORITIES:
            task["priority"] = priority
    task["updated_at"] = datetime.now(UTC).isoformat()
    _save_crm_task(task)
    log_admin_action(
        actor=_audit_actor(request),
        action="crm_task.update",
        target_type="crm_task",
        target_id=task_id,
        details={"fields": sorted(k for k in data if isinstance(k, str))},
        request=request,
    )
    return {"success": True, "task": _serialize_crm_task(task)}


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

        result = send_custom_email(
            to=to, customer_name=customer_name, subject=subject, message=message
        )
        # Audit the send WITHOUT recording the recipient address, subject, or
        # body (all potential PII). A salted-free SHA256 prefix of the lowercased
        # recipient lets us correlate repeated sends to the same address for
        # abuse review without ever persisting the address itself.
        recipient_fp = hashlib.sha256(to.lower().encode("utf-8")).hexdigest()[:12]
        log_admin_action(
            actor=_audit_actor(request),
            action="email.send",
            target_type="email",
            target_id=recipient_fp,
            details={
                "recipient_fingerprint": recipient_fp,
                "subject_len": len(subject),
                "message_len": len(message),
                "delivered": bool(result.get("success")) if isinstance(result, dict) else None,
            },
            request=request,
        )
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
                            "timestamp": m.timestamp,
                        }
                        for m in session.messages[-50:]  # Last 50 messages
                    ],
                },
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
                    "lead_id": s.lead_id,
                }
                for s in sessions
            ],
            "count": len(sessions),
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


def _verify_admin_pin_value(pin: str) -> bool:
    """Verify a submitted PIN against ADMIN_PIN_HASH (constant-time).

    Backward-compatible with the legacy unsalted SHA-256 hex hash (64 hex
    chars), so existing deployments keep working unchanged. New deployments
    should set a salted scrypt hash of the form
    ``scrypt$<n>$<r>$<p>$<salt_b64>$<dk_b64>`` — a slow, salted KDF that resists
    brute-forcing a short numeric PIN far better than fast SHA-256 if the hash
    ever leaks. Generate one with ``scripts/generate_admin_pin_hash.py``.
    """
    stored = ADMIN_PIN_HASH
    if not stored or not pin:
        return False
    if stored.startswith("scrypt$"):
        try:
            _tag, n_s, r_s, p_s, salt_b64, dk_b64 = stored.split("$")
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(dk_b64)
            derived = hashlib.scrypt(
                pin.encode(),
                salt=salt,
                n=int(n_s),
                r=int(r_s),
                p=int(p_s),
                dklen=len(expected),
                maxmem=128 * int(n_s) * int(r_s) + (1 << 20),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(derived, expected)
    # Legacy: unsalted SHA-256 hex.
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    return secrets.compare_digest(pin_hash, stored)


@app.post("/api/admin/verify")
@limiter.limit("5/minute")
async def verify_admin_pin(request: Request):
    """Validate admin PIN server-side and set an httpOnly session cookie.

    Layered defenses:

    * slowapi caps this endpoint at 5/min per IP — short-circuits a fast
      brute-force attacker before they can spin through the 10-attempt
      Redis-backed pin-attempts window.
    * Redis-backed pin-attempts lockout (10 attempts → 5-minute cool-off)
      still applies for clients staying under the 5/min slowapi cap.
    """
    client_ip = _get_client_ip(request)

    # Check brute-force lockout
    now = time.time()
    attempts = _get_pin_attempts(client_ip)
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
        return JSONResponse(
            {"success": False, "error": "Admin auth not configured."}, status_code=503
        )
    if not _verify_admin_pin_value(pin):
        _add_pin_attempt(client_ip, now)
        remaining = PIN_MAX_ATTEMPTS - len(_get_pin_attempts(client_ip))
        struct_logger.warning("Admin login failed", client_ip=client_ip, remaining=remaining)
        return JSONResponse({"success": False, "error": "Incorrect PIN."}, status_code=401)
    # Successful login — clear attempt history
    _clear_pin_attempts(client_ip)
    token = _create_admin_token()
    csrf_token = create_csrf_token()
    struct_logger.info("Admin login succeeded", client_ip=client_ip)
    # Audit trail: track who logged in and when. Use a hash of the freshly
    # minted token as the actor id so the entry is correlatable with later
    # mutations from the same session, without persisting the bearer value.
    token_actor = f"admin:{hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]}"
    log_admin_action(
        actor=token_actor,
        action="admin.login",
        target_type="session",
        target_id=token_actor,
        details={"client_ip": client_ip},
        request=request,
    )
    response = JSONResponse({"success": True, "csrf_token": csrf_token})
    response.set_cookie(
        key="tho_admin_token",
        value=token,
        httponly=True,
        secure=not IS_LOCAL,
        samesite="strict",
        max_age=ADMIN_TOKEN_TTL,
    )
    set_csrf_cookie(
        response,
        csrf_token,
        secure=not IS_LOCAL,
        max_age=ADMIN_TOKEN_TTL,
    )
    return response


@app.get("/api/admin/check")
@limiter.limit("30/minute")
async def check_admin_token(request: Request):
    """Verify that an admin token is still valid (stateless — works across instances)."""
    if _verify_passkey_cookie(request):
        return {"valid": True}
    token = request.cookies.get("tho_admin_token", "")
    if not token:
        token = _admin_token_from_request(request)
    if _verify_admin_token(token):
        return {"valid": True}
    return {"valid": False}


@app.get("/api/admin/audit-log", dependencies=[Depends(require_admin)])
async def get_admin_audit_log(
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
):
    """Return admin audit-log entries in reverse-chronological order.

    Filterable by actor, action, target_type, target_id, and a `since`
    ISO8601 timestamp lower bound. `limit` is clamped to [1, 500].
    """
    try:
        # Validate enum-shaped params so a typo at the call site comes back
        # as a clear 400 instead of a silently empty result set.
        if action and action not in AUDIT_ALLOWED_ACTIONS:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid action. Must be one of: {list(AUDIT_ALLOWED_ACTIONS)}",
                },
                status_code=400,
            )
        if target_type and target_type not in AUDIT_ALLOWED_TARGET_TYPES:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid target_type. Must be one of: {list(AUDIT_ALLOWED_TARGET_TYPES)}",
                },
                status_code=400,
            )

        entries = query_audit_log(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            since=since,
            limit=limit,
        )
        return {"success": True, "entries": entries, "count": len(entries)}
    except Exception as e:
        struct_logger.error("Audit log query failed", error=str(e))
        return {"success": False, "error": "Failed to load audit log."}


@app.get("/api/admin/email-reply-drafts", dependencies=[Depends(require_admin)])
async def get_admin_email_reply_drafts(
    status: str | None = None,
    limit: int = 50,
):
    """Read-only listing of human-review email reply drafts.

    Filterable by draft ``status``; ``limit`` defaults to 50 (the store
    clamps it to [1, 200]). READ-ONLY: this endpoint never transitions a
    draft, never sends, and never writes an audit entry.
    """
    # Lazy import mirrors the other email-pipeline call sites in this module
    # and keeps the Firestore-backed store off the startup path.
    import email_reply_drafts

    valid_statuses = [
        email_reply_drafts.STATUS_PENDING,
        email_reply_drafts.STATUS_APPROVED,
        email_reply_drafts.STATUS_REJECTED,
        email_reply_drafts.STATUS_SENT,
        email_reply_drafts.STATUS_EXPIRED,
    ]
    try:
        # Validate enum-shaped params so a typo at the call site comes back
        # as a clear 400 instead of a silently empty result set.
        if status and status not in valid_statuses:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid status. Must be one of: {valid_statuses}",
                },
                status_code=400,
            )

        # The strict read must distinguish a real empty queue from a Firestore
        # outage or query failure; staff cannot review drafts they cannot see.
        drafts = email_reply_drafts.list_drafts_strict(status=status, limit=limit)
        return {
            "success": True,
            "drafts": [email_reply_drafts.to_dict(d) for d in drafts],
            "count": len(drafts),
        }
    except Exception as e:
        struct_logger.error("Email reply drafts query failed", error=str(e))
        return {"success": False, "error": "Failed to load email reply drafts."}


@app.get("/api/admin/user-activity", dependencies=[Depends(require_admin)])
async def get_user_activity(
    action: str | None = None,
    session_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
):
    """Return user-activity entries in reverse-chronological order.

    Filterable by action, session_id, and a `since` ISO8601 timestamp lower
    bound. `limit` is clamped to [1, 500].
    """
    from tools.user_activity_log import ALLOWED_ACTIONS as USER_ALLOWED_ACTIONS

    try:
        if action and action not in USER_ALLOWED_ACTIONS:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid action. Must be one of: {list(USER_ALLOWED_ACTIONS)}",
                },
                status_code=400,
            )

        entries = query_user_activity(
            action=action,
            session_id=session_id,
            since=since,
            limit=limit,
        )
        return {"success": True, "entries": entries, "count": len(entries)}
    except Exception as e:
        struct_logger.error("User activity query failed", error=str(e))
        return {"success": False, "error": "Failed to load user activity."}


@app.get("/api/admin/feature-flags", dependencies=[Depends(require_admin)])
async def get_feature_flags():
    """Return all feature flags and their current resolved values.

    Safe for admin dashboards; no secrets are exposed.
    """
    try:
        return {"success": True, "flags": feature_flags.all_flags()}
    except Exception as e:
        struct_logger.error("Feature flags query failed", error=str(e))
        return {"success": False, "error": "Failed to load feature flags."}


@app.get("/api/admin/reviews/config", dependencies=[Depends(require_admin)])
async def get_reviews_config():
    """Config for the staff review-request helper (prefilled sms:/mailto: links).

    Inert until an operator sets ``GOOGLE_REVIEW_LINK`` (the Google Business
    Profile "Ask for reviews" short URL). Reads at call time — env var first,
    then the centralized feature-flag system (``FF_GOOGLE_REVIEW_LINK_VALUE``
    or ``config.yaml``) — so a config flip takes effect without a redeploy.
    When unset the frontend hides the feature entirely.
    """
    try:
        link = (os.environ.get("GOOGLE_REVIEW_LINK") or "").strip()
        if not link:
            link = (feature_flags.get_value("GOOGLE_REVIEW_LINK") or "").strip()
        return {"success": True, "enabled": bool(link), "review_link": link or None}
    except Exception as e:
        struct_logger.error("Reviews config query failed", error=str(e))
        return {"success": False, "error": "Failed to load reviews config."}


# QR-code source tags: alphanumeric + dash only, bounded length. Anything else
# is ignored (tracking is best-effort; the redirect must never depend on it).
_REVIEW_SRC_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")


@app.get("/review")
@limiter.limit("120/minute")
async def review_redirect(request: Request, src: str | None = None):
    """Public QR-code redirect to the Google review link (Celeste's rollout).

    Printed QR codes point at ``/review?src=qr-lot`` etc. on OUR domain so the
    destination stays trackable and re-pointable without reprinting. The
    target comes ONLY from config — env ``GOOGLE_REVIEW_LINK`` first, then the
    feature-flag/config.yaml value (same resolution as
    ``/api/admin/reviews/config``) — request params can never change it (no
    open redirect). Fails closed with 404 when no link is configured.

    ``src`` is tracking-only: validated (alnum + dash, max 32 chars, else
    treated as absent) and recorded as a best-effort ``analytics_events``
    record, same fire-and-forget sink as ``POST /api/analytics``.
    """
    link = (os.environ.get("GOOGLE_REVIEW_LINK") or "").strip()
    if not link:
        link = (feature_flags.get_value("GOOGLE_REVIEW_LINK") or "").strip()
    if not link:
        raise HTTPException(status_code=404, detail="Not found")

    src_clean = src if src and _REVIEW_SRC_RE.match(src) else None
    try:
        struct_logger.info("Review redirect", src=src_clean or "direct")
        if _db and getattr(_db, "db", None):
            _store_analytics_event("review_redirect", {"src": src_clean or "direct"})
    except Exception as e:  # tracking must never break the redirect
        try:
            struct_logger.warning("Review redirect tracking failed", error=str(e))
        except Exception:
            pass
    return RedirectResponse(link, status_code=302)


# ─── Customer API (migrated FastContract records) ────────────────────────────


def _strip_ssn_from_customer(c: dict) -> dict:
    """Remove SSN hashes from customer data before sending to frontend."""
    safe = {k: v for k, v in c.items() if k not in ("ssn_hash",)}
    if c.get("co_buyer") and isinstance(c["co_buyer"], dict):
        safe["co_buyer"] = {k: v for k, v in c["co_buyer"].items() if k != "ssn_hash"}
    return safe


_CUSTOMER_SENSITIVE_KEYS = {
    "ssn",
    "ssn_hash",
    "buyer_ssn",
    "co_buyer_ssn",
    "social_security_number",
    "social_security",
}


def _strip_sensitive_customer_payload(value):
    """Recursively drop raw SSN-shaped keys before customer persistence."""
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            if key.lower() in _CUSTOMER_SENSITIVE_KEYS:
                continue
            cleaned[key] = _strip_sensitive_customer_payload(child)
        return cleaned
    if isinstance(value, list):
        return [_strip_sensitive_customer_payload(item) for item in value[:20]]
    return value


def _masked_ssn(value: str | None) -> str:
    """Store only a masked SSN preview for operator matching."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) >= 4:
        return f"***-**-{digits[-4:]}"
    return _sanitize_text(value or "", 20)


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
        return {"customers": [], "total": 0, "source": "firestore", "error": "Search failed"}


@app.get("/api/customers/stats", dependencies=[Depends(require_admin)])
async def customer_stats():
    """Get customer statistics from Firestore."""
    try:
        counts = _db.count_customers()
        return {"migrated": True, **counts}
    except Exception as e:
        logger.error(f"Customer stats failed: {e}")
        return {"migrated": False, "error": "Stats unavailable"}


@app.get("/api/customers/count", dependencies=[Depends(require_admin)])
async def customer_count():
    """Get total customer count from Firestore."""
    try:
        return _db.count_customers()
    except Exception as e:
        logger.error(f"Customer count failed: {e}")
        return {"total": 0, "by_status": {}, "error": "Count unavailable"}


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
                recent_leads.append(
                    {
                        "name": c.get("full_name"),
                        "phone": c.get("phone"),
                        "city": city,
                        "created": str(created)[:10] if created else None,
                    }
                )

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
        return {"error": "Request failed"}


@app.get("/api/admin/crm/funnel", dependencies=[Depends(require_admin)])
async def admin_crm_funnel():
    """
    CRM funnel analytics — customer counts per stage, conversion rates,
    median time-in-stage. Pulls from `customers` and `deals` collections.

    Response shape:
      {
        "stages": [
          {"key": "LEAD", "label": "Lead", "count": N, "conversion_pct": 100.0},
          {"key": "ENROLLED", "label": "Enrolled", "count": N, "conversion_pct": ...},
          {"key": "DEAL", "label": "Deal (active)", "count": N, "conversion_pct": ...},
          {"key": "CLOSED", "label": "Closed", "count": N, "conversion_pct": ...}
        ],
        "median_days_in_stage": {"LEAD_to_ENROLLED": ..., ...},
        "totals": {...}
      }

    5-minute cache.
    """
    from caching import cache_get, cache_set

    cache_key = "admin_crm_funnel_v1"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        import statistics as _stats
        from datetime import datetime

        # Customers (1,963 docs in prod) — paged via search_customers helper
        customers = _db.search_customers(limit=10000)

        lead_count = 0
        enrolled_count = 0
        sold_customer_count = 0
        # For median time LEAD → ENROLLED, we don't have per-stage timestamps;
        # use updated_at - created_at as a coarse proxy when status != LEAD.
        lead_to_enrolled_days: list[float] = []
        for c in customers:
            status = (c.get("status") or "").upper()
            if status == "LEAD":
                lead_count += 1
            elif status == "ENROLLED":
                enrolled_count += 1
            elif status == "SOLD":
                sold_customer_count += 1
            if status in ("ENROLLED", "SOLD"):
                created = _parse_iso_datetime(c.get("created_at"))
                updated = _parse_iso_datetime(c.get("updated_at"))
                if created and updated and updated >= created:
                    lead_to_enrolled_days.append((updated - created).total_seconds() / 86400.0)

        # Deals — count by status. Active deal stages: pending/approved/contract.
        # Closed stages: funded/complete.
        active_deal_count = 0
        closed_deal_count = 0
        denied_count = 0
        deal_close_days: list[float] = []
        # Stream deals collection directly to avoid the 50-record cap of search_deals
        for doc in _db.db.collection("deals").stream(timeout=FIRESTORE_RPC_TIMEOUT):
            data = doc.to_dict() or {}
            status = (data.get("status") or "").lower()
            if status in ("pending", "approved", "contract"):
                active_deal_count += 1
            elif status in ("funded", "complete"):
                closed_deal_count += 1
                created = _parse_iso_datetime(data.get("created_at"))
                updated = _parse_iso_datetime(data.get("updated_at"))
                if created and updated and updated >= created:
                    deal_close_days.append((updated - created).total_seconds() / 86400.0)
            elif status == "denied":
                denied_count += 1

        # Conversion percentages relative to lead_count (top of funnel).
        # ENROLLED count includes everyone past lead; SOLD/closed deal counts
        # include everyone who reached or passed the closed stage.
        top = max(lead_count + enrolled_count + sold_customer_count, 1)
        funnel_lead = lead_count + enrolled_count + sold_customer_count
        funnel_enrolled = enrolled_count + sold_customer_count
        funnel_deal = active_deal_count + closed_deal_count
        funnel_closed = closed_deal_count + sold_customer_count

        def pct(n: int, d: int) -> float:
            return round(100.0 * n / d, 1) if d else 0.0

        stages = [
            {
                "key": "LEAD",
                "label": "Lead",
                "count": funnel_lead,
                "conversion_pct": pct(funnel_lead, top),
            },
            {
                "key": "ENROLLED",
                "label": "Enrolled",
                "count": funnel_enrolled,
                "conversion_pct": pct(funnel_enrolled, top),
            },
            {
                "key": "DEAL",
                "label": "Deal (active)",
                "count": funnel_deal,
                "conversion_pct": pct(funnel_deal, top),
            },
            {
                "key": "CLOSED",
                "label": "Closed",
                "count": funnel_closed,
                "conversion_pct": pct(funnel_closed, top),
            },
        ]

        def _median(xs: list[float]):
            return round(_stats.median(xs), 1) if xs else None

        result = {
            "success": True,
            "stages": stages,
            "median_days_in_stage": {
                "LEAD_to_ENROLLED": _median(lead_to_enrolled_days),
                "DEAL_to_CLOSED": _median(deal_close_days),
            },
            "totals": {
                "customers_total": len(customers),
                "deals_active": active_deal_count,
                "deals_closed": closed_deal_count,
                "deals_denied": denied_count,
            },
            "generated_at": datetime.now(UTC).isoformat(),
        }
        cache_set(cache_key, result, ttl_seconds=300)
        return result
    except Exception as e:
        struct_logger.error("CRM funnel analytics failed", error=str(e))
        return {"success": False, "error": "Failed to compute CRM funnel."}


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

    return re.sub(r"<[^>]+>", "", val).strip()[:max_len]


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
        "legacy_source": data.get("legacy_source", "manual")
        if data.get("legacy_source") in ("manual", "fastcontract", "import")
        else "manual",
        "full_name": name,
        "_name_lower": name.lower().strip(),
        "email": (data.get("email") or "").strip().lower()[:200] or None,
        "phone": (data.get("phone") or "").strip()[:20] or None,
        "status": status,
        "address": _sanitize_text(data.get("address") or "", 300) or None,
        "city": _sanitize_text(data.get("city") or "", 100) or None,
        "state": _sanitize_text(data.get("state") or "TX", 2) or "TX",
        "zip_code": _sanitize_text(data.get("zip_code") or data.get("zip") or "", 10) or None,
        "marital_status": _sanitize_text(data.get("marital_status") or "", 50) or None,
        "employer": _sanitize_text(data.get("employer") or "", 200) or None,
        "occupation": _sanitize_text(data.get("occupation") or "", 200) or None,
        "salesrep": _sanitize_text(data.get("salesrep") or "", 100) or None,
        "notes": _sanitize_text(data.get("notes") or "", 2000) or None,
        "ssn_masked": _masked_ssn(data.get("ssn_masked") or data.get("buyer_ssn")),
        "co_buyer": _strip_sensitive_customer_payload(data.get("co_buyer") or {}),
        "references": _strip_sensitive_customer_payload(data.get("references") or []),
    }

    try:
        doc_id = _db.create_customer(customer, doc_id=customer_id)
        customer["id"] = doc_id
        log_admin_action(
            actor=_audit_actor(request),
            action="customer.create",
            target_type="customer",
            target_id=str(doc_id),
            details={"fields": sorted(k for k in data.keys() if isinstance(k, str))},
            request=request,
        )
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

    updatable = [
        "full_name",
        "email",
        "phone",
        "status",
        "address",
        "city",
        "state",
        "zip_code",
        "marital_status",
        "employer",
        "occupation",
        "salesrep",
        "notes",
        "co_buyer",
        "references",
    ]
    update_data = {k: data[k] for k in updatable if k in data}
    if "full_name" in update_data:
        update_data["_name_lower"] = _sanitize_text(str(update_data["full_name"]), 500).lower()
    if "co_buyer" in update_data:
        update_data["co_buyer"] = _strip_sensitive_customer_payload(update_data["co_buyer"])
    if "references" in update_data:
        update_data["references"] = _strip_sensitive_customer_payload(update_data["references"])

    if not update_data:
        raise HTTPException(400, "No updatable fields provided")

    try:
        _db.update_customer(customer_id, update_data)
        updated = _db.get_customer(customer_id)
        log_admin_action(
            actor=_audit_actor(request),
            action="customer.update",
            target_type="customer",
            target_id=str(customer_id),
            details={"fields": sorted(k for k in update_data.keys() if isinstance(k, str))},
            request=request,
        )
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

    log_user_action(
        action="feedback.submit",
        details={"page": feedback["page"]},
        request=request,
    )

    logger.info(f"Feedback received: {feedback['description'][:100]}")
    return {"success": True, "message": "Thank you for your feedback!"}


# ─── Document History ────────────────────────────────────────────────────────


@app.get("/api/documents/history", dependencies=[Depends(require_admin)])
async def document_history(include_test: bool = False):
    """List generated documents. Merges local and GCS results."""
    docs, _local_count, _gcs_count = _collect_generated_documents()
    visible_docs = docs if include_test else _visible_generated_documents(docs)
    return {
        "documents": visible_docs[:50],
        "total": len(visible_docs),
        "total_including_test": len(docs),
        "hidden_test_document_count": len(docs) - len(_visible_generated_documents(docs)),
        "hidden_document_count": len(docs) - len(_visible_generated_documents(docs)),
        "hidden_quality_document_count": sum(1 for doc in docs if doc.get("quality_blocked")),
        "hidden_legacy_document_count": sum(1 for doc in docs if doc.get("legacy_unverified")),
    }


# ─── Signed-URL Document Share ───────────────────────────────────────────────
# Lights up email-doc-delivery: returns a time-limited V4 signed URL for a
# generated PDF in the `generated_docs/` GCS prefix so customers can be sent a
# link instead of an authenticated path.

_DOCUMENTS_SHARE_DEFAULT_TTL_HOURS = 24
_DOCUMENTS_SHARE_MAX_TTL_HOURS = 72


def _generate_document_signed_url(blob, *, expiration, method: str = "GET") -> str:
    """Generate a GCS signed URL in local-key and Cloud Run ADC environments."""
    try:
        return blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method=method,
        )
    except AttributeError as exc:
        message = str(exc).lower()
        if "private key" not in message and "sign credentials" not in message:
            raise

        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest

        credentials, _project_id = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(GoogleAuthRequest())
        access_token = getattr(credentials, "token", None)
        service_account_email = getattr(credentials, "service_account_email", None)
        if not access_token or not service_account_email or service_account_email == "default":
            raise RuntimeError("Could not resolve service account signing identity") from exc

        return blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method=method,
            service_account_email=service_account_email,
            access_token=access_token,
        )


@app.get("/api/documents/share/{filename}", dependencies=[Depends(require_admin)])
async def share_document(
    filename: str, request: Request, ttl_hours: int = _DOCUMENTS_SHARE_DEFAULT_TTL_HOURS
):
    """Mint a V4 GCS signed URL for a generated PDF (admin-only).

    The filename is constrained to the `generated_docs/` prefix and bare basenames
    only — no path traversal, no slashes. Default TTL is 24 hours; configurable
    up to 72 hours via ``?ttl_hours=N``.
    """
    # ── Filename validation: bare basename only, must be a .pdf ──
    safe_filename = os.path.basename(filename)
    if not safe_filename or safe_filename != filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    if not safe_filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Only PDF files are shareable."}, status_code=400)

    # ── TTL validation ──
    if (
        not isinstance(ttl_hours, int)
        or ttl_hours < 1
        or ttl_hours > _DOCUMENTS_SHARE_MAX_TTL_HOURS
    ):
        return JSONResponse(
            {
                "error": (
                    f"ttl_hours must be an integer between 1 and "
                    f"{_DOCUMENTS_SHARE_MAX_TTL_HOURS}."
                )
            },
            status_code=400,
        )

    # ── GCS lookup + sign ──
    try:
        from google.cloud import storage as _storage
    except ImportError:
        return JSONResponse({"error": "GCS client not available"}, status_code=503)

    bucket_name = os.getenv("GCS_DOCUMENTS_BUCKET", "tho-secure-documents")
    object_key = f"generated_docs/{safe_filename}"

    try:
        client = _storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_key)
        if not blob.exists():
            return JSONResponse({"error": "File not found"}, status_code=404)
        from datetime import timedelta as _timedelta

        expiration = _timedelta(hours=ttl_hours)
        signed_url = _generate_document_signed_url(blob, expiration=expiration, method="GET")
    except Exception as exc:  # noqa: BLE001
        logger.exception("share_document failed for %s", object_key)
        return JSONResponse(
            {"error": f"Failed to generate signed URL: {type(exc).__name__}"},
            status_code=500,
        )

    # ── Soft-import audit log; never hard-fail if it isn't deployed yet. ──
    try:
        from audit_log import log_admin_action  # type: ignore[import-not-found]

        try:
            log_admin_action(
                action="documents.share",
                actor_ip=getattr(request.client, "host", None) if request.client else None,
                target=object_key,
                details={"ttl_hours": ttl_hours, "bucket": bucket_name},
            )
        except Exception:  # noqa: BLE001
            logger.exception("audit_log.log_admin_action failed")
    except ImportError:
        pass

    return {
        "filename": safe_filename,
        "gcs_uri": f"gs://{bucket_name}/{object_key}",
        "signed_url": signed_url,
        "ttl_hours": ttl_hours,
        "expires_in_seconds": ttl_hours * 3600,
        "expires_at": (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat(),
    }


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
    return {field: _json_safe(safe.get(field)) for field in allowed_fields if field in safe}


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
    return {field: _json_safe(lead.get(field)) for field in allowed_fields if field in lead}


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

    for doc in _db.db.collection(collection_name).stream(timeout=FIRESTORE_RPC_TIMEOUT):
        total += 1
        data = doc.to_dict() or {}
        status = data.get(status_field) or "UNKNOWN"
        by_status[status] = by_status.get(status, 0) + 1

    return {"total": total, "by_status": by_status}


@app.get("/api/v1/customers", dependencies=[Depends(require_partner_api_key)])
@limiter.limit("60/minute")
async def v1_list_customers(
    request: Request, limit: int = 50, offset: int = 0, status: str = None, q: str = None
):
    """List customers with pagination and default PII redaction."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        customers = _db.search_customers(
            query_text=q or None,
            status=status or None,
            limit=offset + limit,
        )
        page = customers[offset : offset + limit]
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
@limiter.limit("60/minute")
async def v1_get_customer(customer_id: str, request: Request):
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
@limiter.limit("60/minute")
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
            return {k: _drop_ssn(v) for k, v in obj.items() if "ssn" not in str(k).lower()}
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
        partner_fp = _partner_api_key_fingerprint(_extract_partner_api_key(request))
        log_admin_action(
            actor=f"partner:{partner_fp}",
            action="customer.create",
            target_type="customer",
            target_id=str(created_id),
            details={
                "status": status,
                "via": "partner_api",
                "legacy_source": customer["legacy_source"],
            },
            request=request,
        )
        return {"id": created_id}
    except Exception as e:
        struct_logger.error("Partner API customer create failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create customer")


@app.get("/api/v1/inventory", dependencies=[Depends(require_partner_api_key)])
@limiter.limit("60/minute")
async def v1_list_inventory(
    request: Request, limit: int = 50, offset: int = 0, status: str = None, manufacturer: str = None
):
    """List inventory with simple pagination and partner-safe response fields."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        inventory = _db.search_inventory(
            status=status.upper() if status else None,
            manufacturer=manufacturer or None,
            limit=offset + limit,
        )
        page = inventory[offset : offset + limit]
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
@limiter.limit("60/minute")
async def v1_list_leads(request: Request, limit: int = 50, offset: int = 0, status: str = None):
    """List leads with direct contact details removed."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        leads = await lead_manager.list_leads(status=status, limit=offset + limit)
        page = leads[offset : offset + limit]
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
@limiter.limit("60/minute")
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
                .get(timeout=FIRESTORE_RPC_TIMEOUT)
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
            "idempotency_scope": f"{key_fingerprint}:{idempotency_key}"
            if idempotency_key
            else None,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }

    try:
        _db.db.collection("activities").document(activity_id).set(
            activity, timeout=FIRESTORE_RPC_TIMEOUT
        )
        struct_logger.info(
            "Partner webhook activity logged", activity_id=activity_id, deal_id=deal_id
        )
        return {"id": activity_id, "logged": True, "idempotent_replay": False}
    except Exception as e:
        struct_logger.error("Partner webhook logging failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to record webhook activity")


@app.post(
    "/api/v1/service-requests/{request_id}/resolve", dependencies=[Depends(require_partner_api_key)]
)
@limiter.limit("60/minute")
async def v1_service_request_resolve(request: Request, request_id: str):
    """Mark a service request as resolved. Called by Notion when warranty claims close."""
    try:
        doc = (
            _db.db.collection("service_requests")
            .document(request_id)
            .get(timeout=FIRESTORE_RPC_TIMEOUT)
        )
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Service request not found")

        success = _db.update_service_request(request_id, {"status": "resolved"})
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update service request")

        struct_logger.info("Partner resolved service request", request_id=request_id)
        return {"success": True, "id": request_id, "status": "resolved"}
    except HTTPException:
        raise
    except Exception as e:
        struct_logger.error("Failed to resolve service request via partner API", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/v1/stats", dependencies=[Depends(require_partner_api_key)])
@limiter.limit("60/minute")
async def v1_stats(request: Request):
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
@limiter.limit("60/minute")
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


# ============ SECURE HUB (CUSTOMER PORTAL) ============


def _verify_phone_last4(stored_phone, provided) -> bool:
    """True iff the last 4 digits of ``provided`` match the last 4 of ``stored_phone``.

    Customer-portal verification factor: a deal_id / appointment_id is an opaque
    capability, not a credential. The buyer proves ownership with the phone
    number on file (last 4 digits suffice). Requires >= 4 digits on both sides.
    """
    stored = re.sub(r"\D", "", str(stored_phone or ""))
    given = re.sub(r"\D", "", str(provided or ""))
    if len(stored) < 4 or len(given) < 4:
        return False
    return stored[-4:] == given[-4:]


def _deal_phone_ok(deal_data: dict, provided) -> bool:
    """Verify against the deal's buyer OR co-buyer phone."""
    return _verify_phone_last4(deal_data.get("buyer_phone"), provided) or _verify_phone_last4(
        deal_data.get("co_buyer_phone"), provided
    )


_PORTAL_VERIFY_MSG = (
    "Verification failed. Enter the phone number on your application (last 4 digits)."
)


@app.get("/api/v1/customer/deal/{deal_id}")
@limiter.limit("20/minute")
async def get_secure_hub_deal(deal_id: str, request: Request, phone: str = ""):
    """Fetch deal status and documents for the customer portal.

    Requires phone-last-4 verification against the deal's buyer/co-buyer phone:
    a deal_id alone must never expose buyer PII or the closing documents (which
    carry SSN/DOB). A non-existent deal and a wrong phone return the SAME 403, so
    this is not a deal-existence oracle.
    """
    try:
        db = get_database().db  # THODatabase wrapper -> raw Firestore client
        deal = db.collection("deals").document(deal_id).get(timeout=FIRESTORE_RPC_TIMEOUT)
        deal_data = deal.to_dict() if deal.exists else None
        if not deal_data or not _deal_phone_ok(deal_data, phone):
            raise HTTPException(status_code=403, detail=_PORTAL_VERIFY_MSG)

        # Fetch documents (deal_notes of type esign_completed or document_generated)
        docs_query = (
            db.collection("deal_notes")
            .where("deal_id", "==", deal_id)
            .stream(timeout=FIRESTORE_RPC_TIMEOUT)
        )
        documents = []
        for doc in docs_query:
            d = doc.to_dict()
            documents.append(
                {
                    "id": doc.id,
                    "type": d.get("type"),
                    "name": d.get("template_name") or d.get("filename") or "Document",
                    "created_at": d.get("created_at"),
                    "status": "signed" if d.get("type") == "esign_completed" else "generated",
                }
            )

        return {
            "success": True,
            "deal": {
                "id": deal.id,
                "status": deal_data.get("status"),
                "buyer_name": f"{deal_data.get('buyer_first_name', '')} {deal_data.get('buyer_last_name', '')}".strip(),
                "home_model": deal_data.get("inventory_model_name"),
                "created_at": deal_data.get("created_at"),
            },
            "documents": sorted(documents, key=lambda x: x["created_at"], reverse=True),
        }
    except HTTPException:
        raise
    except Exception as e:
        struct_logger.error("Secure Hub deal fetch failed", error=str(e), deal_id=deal_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/customer/deal/{deal_id}/download/{note_id}")
@limiter.limit("20/minute")
async def download_secure_document(deal_id: str, note_id: str, request: Request, phone: str = ""):
    """Generate a signed URL for a secure closing document (carries SSN/DOB).

    Phone-last-4 verification against the parent deal is required BEFORE any
    signed URL is minted, so a leaked (deal_id, note_id) pair cannot be replayed
    anonymously.
    """
    try:
        db = get_database().db  # THODatabase wrapper -> raw Firestore client
        deal = db.collection("deals").document(deal_id).get(timeout=FIRESTORE_RPC_TIMEOUT)
        deal_data = deal.to_dict() if deal.exists else None
        if not deal_data or not _deal_phone_ok(deal_data, phone):
            raise HTTPException(status_code=403, detail=_PORTAL_VERIFY_MSG)

        note = db.collection("deal_notes").document(note_id).get(timeout=FIRESTORE_RPC_TIMEOUT)
        if not note.exists or note.to_dict().get("deal_id") != deal_id:
            raise HTTPException(status_code=404, detail="Document not found")

        data = note.to_dict()
        gcs_uri = data.get("gcs_path") or data.get("gcs_uri")

        if not gcs_uri or not gcs_uri.startswith("gs://"):
            raise HTTPException(status_code=400, detail="Document storage location unknown")

        # Parse bucket and blob
        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1]

        from google.cloud import storage

        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        # Generate signed URL (expires in 15 mins)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=15),
            method="GET",
        )

        return {"success": True, "url": url}
    except HTTPException:
        raise
    except Exception as e:
        struct_logger.error("Signed URL generation failed", error=str(e), note_id=note_id)
        raise HTTPException(status_code=500, detail=str(e))


# AI PM Manager Routes (Linear-inspired)
from google_ads_admin.routes import router as google_ads_admin_router
from pm_routes import router as pm_router

app.include_router(pm_router, dependencies=[Depends(require_admin)])
app.include_router(passkey_router)
app.include_router(google_ads_step_up_router)
app.include_router(google_ads_approval_router)
app.include_router(google_ads_admin_router, dependencies=[Depends(require_admin)])


# SEO surface: robots.txt, sitemap.xml, and per-route head/body injection for
# the SPA shell (see seo_routes.py and docs/SEO_MIGRATION.md).
import seo_routes


def _seo_public_homes() -> dict:
    """Same merged public inventory the browse page renders, for sitemap,
    legacy detail URLs, and crawlable HTML. Loaders are cached upstream."""
    return _resolve_public_inventory_context()


seo_routes.configure(
    get_homes=_seo_public_homes,
    get_canonical_base=lambda: CANONICAL_PUBLIC_URL,
)
app.include_router(seo_routes.router)


# DNS/MX cutover monitoring routes (partner API key auth, HMAC-signed outbound
# webhooks).  Kept separate from the Telegram/Mira workstream by design.
import dns_mx_cutover_routes

app.include_router(
    dns_mx_cutover_routes.router,
    dependencies=[Depends(require_partner_api_key)],
)


# Mira partner-monitoring routes and Telegram notification webhook
from mira_notify import router as mira_notify_router
from mira_routes import public_router as mira_public_router
from mira_routes import router as mira_router
from mira_routes import set_mira_refs

set_mira_refs(APP_STARTED_AT, _metrics_store)
app.include_router(mira_public_router)
app.include_router(mira_router, dependencies=[Depends(require_partner_api_key)])

# Obsidian Sovereign LLM partner-monitoring routes and inbound notification hook
from obsidian_routes import public_router as obsidian_public_router
from obsidian_routes import router as obsidian_router
from obsidian_routes import set_obsidian_refs

set_obsidian_refs(APP_STARTED_AT, _metrics_store)
app.include_router(obsidian_public_router)
app.include_router(obsidian_router, dependencies=[Depends(require_partner_api_key)])
app.include_router(mira_notify_router, dependencies=[Depends(require_partner_api_key)])

# GitHub → Mira trigger bridge (cutover alerts for PR #156 and related events)
from github_mira_trigger import status_router as github_mira_status_router
from github_mira_trigger import webhook_router as github_mira_webhook_router

app.include_router(github_mira_webhook_router)
app.include_router(github_mira_status_router, dependencies=[Depends(require_partner_api_key)])


# Ops Copilot — in-app, admin/employee-only assistant (GCP-native, Vertex/Gemini).
# Read-only v1: answers questions about live business data and explains how to use
# the platform. Replaces the external Telegram/Mira bot with an in-app surface.
from schemas.copilot_schemas import CopilotRequest


@app.get("/api/admin/ops-snapshot", dependencies=[Depends(require_admin)])
async def admin_ops_snapshot():
    """Live, PII-free business snapshot for the admin Ops dashboard.

    Read-only and admin-gated. Aggregates COUNTS only (leads/appointments/
    inventory/deals/installations/feedback, plus the Notion Command Center ops
    counts — delivery/title/collections/insurance — when NOTION_COMMAND_CENTER is
    on). Never any customer identity or dollar figure. Each section is
    fault-isolated; this never 500s. This is the read surface that replaces the
    Telegram/Mira status pushes with an in-app, GCP-native view.
    """
    from tools.ops_copilot import get_business_snapshot

    try:
        return {"success": True, "snapshot": await get_business_snapshot()}
    except Exception as e:
        struct_logger.error("Ops snapshot failed", error=str(e))
        return {"success": False, "error": "Snapshot unavailable."}


@app.post("/api/admin/copilot", dependencies=[Depends(require_admin)])
async def admin_ops_copilot(body: CopilotRequest):
    """Answer a staff question using live business data + platform how-to.

    Admin-gated and read-only — it never writes. On any internal failure it
    returns a friendly reply with ``error: true`` rather than a 500, so the
    chat panel always has something to show.
    """
    from tools.ops_copilot import run_copilot

    history = [turn.model_dump() for turn in body.history]
    return await run_copilot(body.message, history)


# ---------------------------------------------------------------------------
# Email one-time-code admin login (FALLBACK alongside PIN + passkey)
# ---------------------------------------------------------------------------
#
# Shares ONE allowlist with passkeys (auth.routes.is_allowed_admin_email) and
# the SAME brute-force lockout + session minting as /api/admin/verify, so a
# successful code login is honored by /api/admin/check and require_admin.
#
# Defenses, mirroring verify_admin_pin:
#   * slowapi caps /request at 3/min and /verify at 5/min per IP;
#   * the request endpoint NEVER reveals whether an email is authorized
#     (always 200) — no account enumeration;
#   * codes are single-use, hashed at rest, TTL-bound, and per-code
#     attempt-capped; the shared IP pin-attempts lockout still applies.

EMAIL_CODE_RESEND_COOLDOWN_SECONDS = 30


def _email_login_invalid_response() -> JSONResponse:
    """Uniform 401 so callers can't distinguish wrong-code from no-code."""
    return JSONResponse({"success": False, "error": "Invalid or expired code."}, status_code=401)


@app.post("/api/admin/email-code/request")
@limiter.limit("3/minute")
async def request_admin_email_code(request: Request):
    """Email a one-time admin sign-in code to an authorized address.

    Always returns ``{"success": True}`` with status 200 regardless of whether
    the email is authorized — this prevents account enumeration. The code is
    only generated, stored (hashed), and sent for allowlisted emails.
    """
    try:
        data = await request.json()
    except (JSONDecodeError, ValueError):
        return JSONResponse({"success": False, "error": "Malformed JSON body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse(
            {"success": False, "error": "Request body must be a JSON object."},
            status_code=400,
        )
    email = str(data.get("email", "")).strip().lower()
    client_ip = _get_client_ip(request)

    # Generic success envelope — computed once, returned on every path so the
    # response is identical for authorized and unauthorized emails.
    generic_ok = JSONResponse({"success": True})

    if not is_allowed_admin_email(email):
        # No store write, no send, no log of the address — silent for unknowns.
        return generic_ok

    try:
        store = default_code_store()
    except EmailLoginCodeStoreUnavailable as exc:
        struct_logger.warning("Email login-code store unavailable", error=str(exc))
        # Still return the generic envelope so the store backend isn't probeable.
        return generic_ok

    now = time.time()

    # Email-bomb guard: if an unexpired code was issued < cooldown ago, don't
    # re-send (the existing code is still valid). Response stays generic.
    existing = store.get(email)
    if existing is not None and (now - existing.created_at) < EMAIL_CODE_RESEND_COOLDOWN_SECONDS:
        struct_logger.info("Admin email-code resend suppressed (cooldown)", client_ip=client_ip)
        return generic_ok

    code = generate_code()
    store.put(email, hash_code(code), now + EMAIL_CODE_TTL_SECONDS)
    # NEVER log the code. send_admin_login_code logs only subject + recipient.
    send_admin_login_code(email, code, ttl_minutes=EMAIL_CODE_TTL_SECONDS // 60)
    # Actor/target are a salted hash of the email so the audit trail correlates
    # request→login without ever persisting the address in cleartext.
    email_actor = f"email:{hashlib.sha256(email.encode('utf-8')).hexdigest()[:12]}"
    log_admin_action(
        actor=email_actor,
        action="admin.login_code.request",
        target_type="session",
        target_id=email_actor,
        details={"method": "email_code", "client_ip": client_ip},
        request=request,
    )
    return generic_ok


@app.post("/api/admin/email-code/verify")
@limiter.limit("5/minute")
async def verify_admin_email_code(request: Request):
    """Verify an emailed one-time code and mint the standard admin session.

    On success this mints the EXACT same session as ``/api/admin/verify`` (the
    ``tho_admin_token`` + ``tho_csrf_token`` cookies and the ``admin.login``
    audit entry) so ``/api/admin/check`` and ``require_admin`` honor it.
    """
    client_ip = _get_client_ip(request)

    # Shared IP lockout — identical to verify_admin_pin.
    now = time.time()
    attempts = _get_pin_attempts(client_ip)
    if len(attempts) >= PIN_MAX_ATTEMPTS:
        struct_logger.warning(
            "Admin email-code login locked out", client_ip=client_ip, attempts=len(attempts)
        )
        return JSONResponse(
            {"success": False, "error": "Too many failed attempts. Please wait 5 minutes."},
            status_code=429,
            headers={"Retry-After": str(PIN_LOCKOUT_SECONDS)},
        )

    try:
        data = await request.json()
    except (JSONDecodeError, ValueError):
        return JSONResponse({"success": False, "error": "Malformed JSON body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse(
            {"success": False, "error": "Request body must be a JSON object."},
            status_code=400,
        )
    email = str(data.get("email", "")).strip().lower()
    code = data.get("code", "")
    if not isinstance(code, str):
        return JSONResponse({"success": False, "error": "Code must be a string."}, status_code=400)
    code = code.strip()

    # Fail closed BEFORE consuming a code: if admin auth isn't configured, mirror
    # verify_admin_pin's 503 instead of burning the user's valid code on a misconfig.
    if not ADMIN_PIN_HASH:
        struct_logger.warning("Admin email-code login rejected: ADMIN_PIN_HASH not configured")
        return JSONResponse(
            {"success": False, "error": "Admin auth not configured."}, status_code=503
        )
    # Defense in depth: re-assert the shared allowlist on verify so no future
    # store-seeding path could ever mint a session for a non-authorized email.
    if not is_allowed_admin_email(email):
        _add_pin_attempt(client_ip, now)
        return _email_login_invalid_response()

    try:
        store = default_code_store()
    except EmailLoginCodeStoreUnavailable as exc:
        struct_logger.warning("Email login-code store unavailable", error=str(exc))
        return JSONResponse(
            {"success": False, "error": "Admin auth not configured."}, status_code=503
        )

    # No record (never requested / expired / already consumed) → generic 401.
    rec = store.get(email)
    if rec is None:
        _add_pin_attempt(client_ip, now)
        return _email_login_invalid_response()

    # Per-code attempt cap. Count this attempt; once it exceeds the cap, burn
    # the code so an attacker can't keep guessing against the same code.
    code_attempts = store.increment_attempts(email)
    if code_attempts > MAX_CODE_ATTEMPTS:
        store.delete(email)
        _add_pin_attempt(client_ip, now)
        return _email_login_invalid_response()

    if not hmac.compare_digest(hash_code(code), rec.code_hash):
        _add_pin_attempt(client_ip, now)
        return _email_login_invalid_response()

    # Success → single-use: consume the code immediately.
    store.delete(email)
    _clear_pin_attempts(client_ip)

    token = _create_admin_token()
    csrf_token = create_csrf_token()
    struct_logger.info("Admin email-code login succeeded", client_ip=client_ip)
    token_actor = f"admin:{hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]}"
    log_admin_action(
        actor=token_actor,
        action="admin.login",
        target_type="session",
        target_id=token_actor,
        details={"method": "email_code", "client_ip": client_ip},
        request=request,
    )
    response = JSONResponse({"success": True, "csrf_token": csrf_token})
    response.set_cookie(
        key="tho_admin_token",
        value=token,
        httponly=True,
        secure=not IS_LOCAL,
        samesite="strict",
        max_age=ADMIN_TOKEN_TTL,
    )
    set_csrf_cookie(
        response,
        csrf_token,
        secure=not IS_LOCAL,
        max_age=ADMIN_TOKEN_TTL,
    )
    return response


# Serve Frontend — Must be last to avoid catching API routes
app.mount("/assets", ImmutableStaticFiles(directory="frontend/dist/assets"), name="assets")


# HEAD is included because uptime monitors default to HEAD on "/" and the
# Cloud Run service should not answer the probe with 405.
@app.get("/{full_path:path}")
@app.head("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        # Funnel unknown /api/* paths through the resilient HTTPException
        # handler so they get the {success, status_code, message} envelope and
        # the no-cache Cache-Control header. Preserves PR #17 behaviour
        # (JSON 404, no SPA fallback).
        raise HTTPException(status_code=404, detail="Not Found")

    # SEO-aware rendering: known public/admin routes, legacy detail URLs, and
    # quote redirects. Returns None for paths that may be real dist files.
    seo_response = seo_routes.render_spa_response(full_path)
    if seo_response is not None:
        return seo_response

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
    # Unknown route and not a dist file: real 404 with the SPA shell so
    # crawlers don't index every typo as a page (soft-404 avoidance).
    return seo_routes.render_not_found()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
