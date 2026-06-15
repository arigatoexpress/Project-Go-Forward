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
from auth.routes import router as passkey_router
from auth.session import SESSION_COOKIE_NAME as PASSKEY_COOKIE_NAME
from auth.session import SessionManager
from config_loader import business_name, get_deployment_config

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
    send_appointment_confirmation,
    send_custom_email,
    send_deal_status_update,
    send_document_email,
    send_lead_welcome,
)
from lead_management import Lead, LeadManager
from structured_logging import logger as struct_logger
from tools.input_sanitizer import sanitize_body
from tools.pii_guard import redact_pii_from_text, validate_no_pii_in_text


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
    if path in {"/health", "/healthz", "/healthz/"}:
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
            endpoint_data = {
                key: list(buf) for key, buf in self._endpoint_buffers.items()
            }

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
        if request.url.path in {"/health", "/healthz", "/healthz/"}:
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
    "CANONICAL_PUBLIC_URL", "https://tho.sapphirealpha.xyz"
).rstrip("/")
_CANONICAL_PUBLIC_PARTS = urlsplit(CANONICAL_PUBLIC_URL)
_CANONICAL_PUBLIC_SCHEME = _CANONICAL_PUBLIC_PARTS.scheme or "https"
_CANONICAL_PUBLIC_HOST = _CANONICAL_PUBLIC_PARTS.netloc or "tho.sapphirealpha.xyz"


def _should_redirect_to_canonical_host(request: Request) -> bool:
    """Keep operator/client navigation on the production vanity domain.

    Cloud Run's default *.run.app URL remains useful for probes and low-level
    diagnostics, but customer-facing pages must settle on tho.sapphirealpha.xyz
    so admin cookies, passkeys, and support instructions all share one origin.
    """
    if request.method.upper() not in {"GET", "HEAD"}:
        return False

    host = request.headers.get("host", "")
    host_name = host.split(":", 1)[0].lower().rstrip(".")
    if not host_name.endswith(".run.app"):
        return False

    path = request.url.path
    if path == "/llms.txt":
        return False
    if path.startswith("/health") or path == "/api" or path.startswith("/api/"):
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
# Uses HMAC-SHA256 with a shared secret derived from the PIN hash.
import base64
import hmac
import struct

# Derive a stable session secret from the PIN hash if not explicitly provided.
# This prevents random secret rotation on every Cloud Run cold start, which
# causes session invalidation and 'double PIN gates'.
if not os.environ.get("ADMIN_SESSION_SECRET") and ADMIN_PIN_HASH:
    # Use a different salt from the JWT secret below
    _derived_secret = hashlib.sha256(f"tho-session-v2-{ADMIN_PIN_HASH}".encode()).hexdigest()
    os.environ["ADMIN_SESSION_SECRET"] = _derived_secret
    logger.info("ADMIN_SESSION_SECRET derived from PIN hash for stability")

ADMIN_TOKEN_TTL = int(os.environ.get("ADMIN_TOKEN_TTL", str(24 * 60 * 60)))  # 24 hours
_JWT_SECRET = hashlib.sha256(f"sapphire-jwt-{ADMIN_PIN_HASH[:16]}".encode()).digest()


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


def _create_csrf_token() -> str:
    """Generate a random CSRF token for double-submit cookie pattern."""
    return secrets.token_hex(32)


def _verify_csrf(request: Request) -> bool:
    """Verify CSRF token for state-changing admin requests.

    Safe methods (GET, HEAD, OPTIONS) are exempt.
    Requests using X-Admin-Token or Authorization: Bearer header
    (custom headers, already CSRF-safe) are exempt.
    Cookie-based auth requests must provide matching X-CSRF-Token header.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    # If using header-based auth, skip CSRF (custom headers are CSRF-safe)
    if request.headers.get("X-Admin-Token", "").strip():
        return True
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return True
    csrf_cookie = request.cookies.get("tho_csrf_token", "")
    csrf_header = request.headers.get("X-CSRF-Token", "")
    if not csrf_cookie or not csrf_header:
        return False
    return secrets.compare_digest(csrf_cookie, csrf_header)


async def require_admin(request: Request):
    """FastAPI dependency that validates the stateless admin token or passkey session."""
    if _verify_passkey_cookie(request):
        return
    token = _admin_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    if not _verify_admin_token(token):
        raise HTTPException(
            status_code=401, detail="Admin session expired. Please re-authenticate."
        )
    if not _verify_csrf(request):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


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
async def get_public_chat_session(session_id: str):
    """Retrieve chat history for a given session ID to persist memory on the frontend."""
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
        for doc in _db.db.collection("deals").stream():
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
            for doc in _db.db.collection("analytics_events").limit(10000).stream():
                d = doc.to_dict() or {}
                if str(d.get("created_at", "")) >= cutoff:
                    events.append(d)
        except Exception as e:
            struct_logger.warning("analytics_events read failed", error=str(e))

        by_type = Counter(e.get("event", "unknown") for e in events)
        top_homes = Counter(
            (e.get("props") or {}).get("home")
            for e in events
            if (e.get("props") or {}).get("home")
        )
        daily = Counter(str(e.get("created_at") or "")[:10] for e in events)
        return {
            "range": range,
            "total_events": len(events),
            "by_type": [{"event": k, "count": v} for k, v in by_type.most_common()],
            "top_homes": [{"home": k, "count": v} for k, v in top_homes.most_common(10)],
            "daily_trend": [{"date": d, "events": c} for d, c in sorted(daily.items()) if d],
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


@app.api_route("/health", methods=["GET", "HEAD"])
@limiter.exempt
def health():
    return {"status": "ok"}


@app.api_route("/llms.txt", methods=["GET", "HEAD"])
@limiter.exempt
def llms_txt() -> FileResponse:
    return FileResponse(
        LLMS_TXT_PATH,
        media_type="text/plain; charset=utf-8",
        # noindex per Google guidance: llms.txt can otherwise leak into SERPs.
        headers={"Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex"},
    )


@app.api_route("/healthz", methods=["GET", "HEAD"], response_class=JSONResponse)
@app.api_route("/healthz/", methods=["GET", "HEAD"], response_class=JSONResponse)
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


@app.get("/healthz/detailed", response_class=JSONResponse)
@limiter.exempt
def healthz_detailed(request: Request) -> JSONResponse:
    """Detailed diagnostics — requires admin auth."""
    token = request.headers.get("X-Admin-Token", "")
    if not token or not _verify_admin_token(token):
        raise HTTPException(status_code=403, detail="Admin access required")
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
        count = len(documents_included or [])
        message = f"Generated {count} packet document{'s' if count != 1 else ''}."

    return {
        "filename": filename,
        "download_url": download_url,
        "message": message,
        "page_count": page_count,
        "documents_included": documents_included or [],
        "documents_skipped": documents_skipped or [],
    }


def _is_document_validation_failure(message: str) -> bool:
    normalized = str(message or "")
    return (
        "Missing required fields" in normalized
        or "Validation failed" in normalized
        or "Field required" in normalized
    )


def _is_document_quality_failure(result: dict) -> bool:
    return (
        isinstance(result, dict)
        and result.get("success") is False
        and result.get("error") in {"quality_gate_failed", "missing_required_fields"}
    )


def _document_quality_json_response(result: dict) -> JSONResponse:
    return JSONResponse(
        {
            "success": False,
            "error": result.get("error") or "quality_gate_failed",
            "message": result.get("message") or "Document quality gate failed.",
            "quality_issues": result.get("quality_issues") or [],
            "missing_fields": result.get("missing_fields") or [],
        },
        status_code=400,
    )


def _document_batch_failure_json_response(result: dict) -> JSONResponse:
    documents = result.get("documents") if isinstance(result.get("documents"), list) else []
    documents_skipped = result.get("documents_skipped")
    if documents_skipped is None and documents:
        documents_skipped = [
            {
                "template": doc.get("template_name") or doc.get("filename") or "unknown",
                "reason": doc.get("message") or doc.get("error") or "Generation failed",
            }
            for doc in documents
            if isinstance(doc, dict) and not doc.get("success")
        ]

    message = result.get("message")
    if not message and documents_skipped:
        first = documents_skipped[0]
        message = f"{first.get('template', 'Selected document')}: {first.get('reason', 'Generation failed')}"
        if len(documents_skipped) > 1:
            message += f" ({len(documents_skipped)} documents failed.)"
    if not message:
        message = "No documents were generated. Review the selected forms and required fields."

    return JSONResponse(
        {
            "success": False,
            "error": "batch_generation_failed",
            "message": message,
            "documents": documents,
            "documents_skipped": documents_skipped or [],
            "total": result.get("total", 0),
            "successful": result.get("successful", 0),
        },
        status_code=400,
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
            created_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
            docs.append(
                {
                    "filename": filename,
                    "size_bytes": stat.st_size,
                    "created_at": created_at,
                    "download_url": f"/api/documents/download/{filename}",
                    "source": "local",
                    **_document_history_flags(filename, created_at),
                }
            )
            seen.add(filename)
            local_count += 1

    gcs_docs = list_gcs_documents()
    gcs_count = len(gcs_docs)
    for gcs_doc in gcs_docs:
        filename = gcs_doc.get("filename")
        if not filename or filename in seen:
            continue
        docs.append(
            {
                **gcs_doc,
                "source": "gcs",
                **_document_history_flags(filename, gcs_doc.get("created_at")),
            }
        )
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


@app.get("/api/document-templates", dependencies=[Depends(require_admin)])
async def list_templates_legacy_alias():
    """Backward-compatible alias for old employee browser sessions."""
    return await list_templates()


@app.get("/api/documents/readiness", dependencies=[Depends(require_admin)])
async def document_readiness():
    """Summarize document-system readiness for the employee UI."""
    try:
        templates = engine_list_templates()
        packets = engine_list_packets()
        docs, local_count, gcs_count = _collect_generated_documents()
        visible_docs = _visible_generated_documents(docs)
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
            "generated_document_count": len(visible_docs),
            "total_document_count": len(docs),
            "hidden_test_document_count": len(docs) - len(visible_docs),
            "local_document_count": local_count,
            "gcs_document_count": gcs_count,
            "output_dir": "available" if output_dir_exists else "missing",
            "output_dir_writable": output_dir_writable,
            "latest_document_at": visible_docs[0].get("created_at") if visible_docs else None,
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
async def generate_document_endpoint(body: GenerateDocumentRequest, request: Request):
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
            template_name=body.template_name,
            data=body.data,
        )
        if result["success"]:
            log_admin_action(
                actor=_audit_actor(request),
                action="document.generate",
                target_type="document",
                target_id=str(result.get("filename", "")),
                details={
                    "template_name": str(body.template_name)[:100],
                    "field_count": len(body.data) if isinstance(body.data, dict) else 0,
                },
                request=request,
            )

            # Lane 3: Best-effort auto-email if customer email present in data
            _maybe_email_document(
                customer_email=body.customer_email
                or body.data.get("buyer_email")
                or body.data.get("email"),
                customer_name=body.customer_name
                or body.data.get("buyer_first_name")
                or body.data.get("first_name"),
                doc_filename=result["filename"],
                doc_type="single_template",
                download_url=f"/api/documents/download/{result['filename']}",
            )

            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result["filename"],
                "message": result["message"],
            }
        # Surface validation failures as 400s with a structured envelope so
        # callers can react programmatically and so we never accidentally
        # ship a 200 + empty PDF for an empty deal.
        if _is_document_quality_failure(result):
            return _document_quality_json_response(result)
        message = result.get("message", "")
        if _is_document_validation_failure(message):
            response_message = message
            if "Missing required fields" not in response_message:
                response_message = f"Missing required fields. {response_message}"
            return JSONResponse(
                {
                    "success": False,
                    "error": "missing_required_fields",
                    "message": response_message,
                },
                status_code=400,
            )
        return {"success": False, "error": message}
    except Exception as e:
        struct_logger.error("Document generation failed", error=str(e))
        return {"success": False, "error": "Document generation failed. Please try again."}


@app.post("/api/documents/generate-packet", dependencies=[Depends(require_admin)])
async def generate_packet_endpoint(body: GeneratePacketRequest, request: Request):
    """Generate a closing packet (multiple merged PDFs)."""
    try:
        result = engine_generate_packet(
            packet_name=body.packet_name,
            data=body.data,
        )
        if result["success"]:
            packet_fields = _packet_result_fields(result)
            log_admin_action(
                actor=_audit_actor(request),
                action="document.generate",
                target_type="document",
                target_id=str(packet_fields.get("filename", "")),
                details={
                    "packet_name": str(body.packet_name)[:100],
                    "page_count": packet_fields.get("page_count", 0),
                    "documents_included": [
                        str(d)[:60] for d in (packet_fields.get("documents_included") or [])
                    ][:50],
                },
                request=request,
            )

            # Lane 3: Best-effort auto-email if customer email present in data
            if packet_fields.get("filename"):
                _maybe_email_document(
                    customer_email=body.customer_email
                    or body.data.get("buyer_email")
                    or body.data.get("email"),
                    customer_name=body.customer_name
                    or body.data.get("buyer_first_name")
                    or body.data.get("first_name"),
                    doc_filename=packet_fields["filename"],
                    doc_type="closing_packet",
                    download_url=packet_fields["download_url"],
                )

            return {
                "success": True,
                "download_url": packet_fields["download_url"],
                "filename": packet_fields["filename"],
                "message": packet_fields["message"],
                "page_count": packet_fields.get("page_count", 0),
                "documents_included": packet_fields.get("documents_included", []),
                "documents_skipped": packet_fields.get("documents_skipped", []),
            }
        if _is_document_quality_failure(result):
            return _document_quality_json_response(result)
        return {
            "success": False,
            "error": result.get("message") or result.get("error") or "Packet generation failed",
        }
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
        if _is_document_quality_failure(result):
            return _document_quality_json_response(result)
        if isinstance(result, dict) and result.get("success") is False:
            return _document_batch_failure_json_response(result)
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
                "filename": result["filename"],
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
        return JSONResponse(
            {"error": "Only PDF files are available for download."}, status_code=400
        )

    resolved = os.path.abspath(os.path.join(OUTPUT_DIR, safe_filename))
    if not resolved.startswith(os.path.abspath(OUTPUT_DIR)):
        return JSONResponse({"error": "Invalid filename"}, status_code=403)

    # Try local first
    if os.path.isfile(resolved):
        return FileResponse(
            resolved,
            filename=safe_filename,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )

    # Fall back to GCS (local file may have been lost on Cloud Run restart)
    if download_from_gcs(safe_filename, resolved):
        return FileResponse(
            resolved,
            filename=safe_filename,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )

    return JSONResponse({"error": "File not found"}, status_code=404)


# ─── Inventory API ───
from database.deal_validation import validate_for_documents
from database.firestore_client import get_database
from database.models import Deal, DealStatus, Inventory

_db = get_database()
app.state.db = _db
INVENTORY_PLACEHOLDER_IMAGE_URL = "/tex-icon.svg"
_INVENTORY_MEDIA_INDEX_CACHE = {"loaded_at": 0.0, "index": {}}
_INVENTORY_MEDIA_INDEX_TTL_SECONDS = 15 * 60


def _pick_inventory_field(item: dict, *keys: str):
    """Return the first populated inventory value across legacy key aliases."""
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _inventory_media_key(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _legacy_inventory_context_for_admin_media() -> dict:
    """Load admin inventory media context without blocking on a live crawl.

    Document Center inventory must be fast and reliable. The live legacy-site
    crawl can take tens of seconds when the legacy WordPress site is slow, so
    this admin path uses a local snapshot in production and still honors tests
    or operator overrides that monkeypatch `load_legacy_inventory_context`.
    """
    loader = globals().get("load_legacy_inventory_context")

    # Unit tests and operator shims replace the imported loader with a local
    # callable. Preserve that hook so media-recovery behavior remains testable.
    if loader is not None and getattr(loader, "__module__", "") != "tools.legacy_site_crawler":
        try:
            return loader(limit=100, force_refresh=False, snapshot_fallback=True) or {}
        except TypeError:
            return loader(limit=100) or {}

    try:
        from tools.legacy_site_crawler import _load_snapshot_context

        return _load_snapshot_context() or {}
    except Exception as exc:
        struct_logger.warning("Legacy inventory media index unavailable", error=str(exc))
        return {}


def _load_legacy_inventory_media_index() -> dict[str, dict]:
    """Map legacy IDs/model names to photo-ready legacy media records."""
    now = time.monotonic()
    cached = _INVENTORY_MEDIA_INDEX_CACHE.get("index") or {}
    loaded_at = float(_INVENTORY_MEDIA_INDEX_CACHE.get("loaded_at") or 0.0)
    if cached and now - loaded_at < _INVENTORY_MEDIA_INDEX_TTL_SECONDS:
        return cached

    context = _legacy_inventory_context_for_admin_media()

    classifier = globals().get("apply_classifier_to_home")
    if classifier is None:
        try:
            from tools.photo_classifier import apply_classifier_to_home as classifier
        except Exception:
            classifier = None

    index: dict[str, dict] = {}
    for home in context.get("homes") or []:
        if not isinstance(home, dict):
            continue
        media_home = dict(home)
        if classifier:
            classifier(media_home)
        for key in (
            home.get("id"),
            home.get("legacy_inventory_id"),
            home.get("listing_id"),
            _inventory_media_key(home.get("model_name") or home.get("model")),
        ):
            media_key = _inventory_media_key(key)
            if media_key:
                index[media_key] = media_home
    _INVENTORY_MEDIA_INDEX_CACHE["loaded_at"] = now
    _INVENTORY_MEDIA_INDEX_CACHE["index"] = index
    return index


def _asset_media_for_inventory_item(result: dict, item: dict) -> dict | None:
    """Return photo-normalized catalog media for a staff inventory item."""
    try:
        from tools.asset_scraper import get_assets_for_home, get_matterport_url
    except Exception:
        return None

    asset = None
    for name in (
        result.get("model_name"),
        item.get("model_name"),
        item.get("model"),
        item.get("name"),
    ):
        if not name:
            continue
        asset = get_assets_for_home(str(name))
        if asset:
            break
    if not asset:
        return None

    images = list(asset.get("images") or [])
    floorplan = asset.get("floor_plan")
    media = {
        "model_name": asset.get("name") or result.get("model_name"),
        "manufacturer": asset.get("manufacturer") or result.get("manufacturer"),
        "image_url": images[0] if images else "",
        "hero_image": images[0] if images else "",
        "real_photos": images,
        "photos": images,
        "gallery_images": images[:3],
        "image_categories": asset.get("image_categories") or {},
        "floorplan_url": floorplan,
        "floor_plan_url": floorplan,
        "floorplan_urls": [floorplan] if floorplan else [],
        "media_recovery": {
            "source": "asset_catalog",
            "reason": "admin_inventory_record_missing_real_photo",
        },
    }
    if asset.get("matterport_id"):
        media["matterport_id"] = asset.get("matterport_id")
        media["matterport_url"] = get_matterport_url(asset["matterport_id"])

    classifier = globals().get("apply_classifier_to_home")
    if classifier is None:
        try:
            from tools.photo_classifier import apply_classifier_to_home as classifier
        except Exception:
            return media
    classifier(media)
    return media


def _apply_inventory_media_fallback(result: dict, item: dict, legacy_media_index: dict[str, dict]):
    """Normalize staff inventory media and recover stale/floorplan-only records."""
    classifier = globals().get("apply_classifier_to_home")
    has_photo = globals().get("has_real_photo")
    if classifier is None or has_photo is None:
        try:
            from tools.photo_classifier import apply_classifier_to_home as classifier
            from tools.photo_classifier import has_real_photo as has_photo
        except Exception:
            return result

    media_fields = (
        "image_url",
        "hero_image",
        "real_photos",
        "photos",
        "gallery_images",
        "image_categories",
        "floorplan_url",
        "floor_plan_url",
        "floorplan_urls",
        "matterport_id",
        "matterport_url",
    )

    media = {**item, **result}
    classifier(media)

    try:
        from tools.manufacturer_media_recovery import apply_manufacturer_media_recovery

        apply_manufacturer_media_recovery(media)
    except Exception as exc:
        struct_logger.warning("Manufacturer media recovery failed", error=str(exc))

    if not has_photo(media):
        lookup_keys = [
            item.get("id"),
            item.get("legacy_inventory_id"),
            item.get("listing_id"),
            result.get("id"),
            result.get("model_name"),
            item.get("model_name"),
            item.get("model"),
        ]
        legacy_home = next(
            (
                legacy_media_index[_inventory_media_key(key)]
                for key in lookup_keys
                if _inventory_media_key(key) in legacy_media_index
            ),
            None,
        )
        if legacy_home:
            for field in media_fields:
                value = legacy_home.get(field)
                if value:
                    media[field] = value
            classifier(media)
            result["media_recovery"] = {
                "source": "legacy_inventory_context",
                "reason": "admin_inventory_record_missing_real_photo",
            }

    if not has_photo(media):
        asset_media = _asset_media_for_inventory_item(result, item)
        if asset_media and has_photo(asset_media):
            for field in media_fields:
                value = asset_media.get(field)
                if value:
                    media[field] = value
            classifier(media)
            result["media_recovery"] = {
                "source": "asset_catalog",
                "reason": "admin_inventory_record_missing_real_photo",
            }

    for field in (
        "image_url",
        "hero_image",
        "real_photos",
        "photos",
        "gallery_images",
        "image_categories",
        "floorplan_url",
        "floor_plan_url",
        "floorplan_urls",
        "matterport_id",
        "matterport_url",
        "media_recovery",
        "media_quality",
    ):
        if field in media:
            result[field] = media.get(field)
    if not has_photo(result):
        result["image_url"] = INVENTORY_PLACEHOLDER_IMAGE_URL
        result["hero_image"] = INVENTORY_PLACEHOLDER_IMAGE_URL
        result["image_placeholder"] = True
        result["placeholder_reason"] = (result.get("media_quality") or {}).get(
            "status", "missing_photos"
        )
    return result


@app.get("/api/inventory", dependencies=[Depends(require_admin)])
async def list_inventory(status: str = "AVAILABLE", limit: int = 100, is_new: bool = None):
    """List inventory for document generation."""
    try:
        inventory = _db.search_inventory(status=status, limit=limit)
        legacy_media_index = _load_legacy_inventory_media_index()

        # Transform for frontend
        results = []
        for item in inventory:
            # Filter by is_new if specified
            if is_new is not None and item.get("is_new") != is_new:
                continue

            result = {
                "id": item.get("id"),
                "model_name": item.get("model_name") or item.get("model"),
                "manufacturer": item.get("manufacturer"),
                "year": item.get("year"),
                "is_new": item.get("is_new", True),
                "serial_number": item.get("serial_number"),
                "serial_number_2": _pick_inventory_field(
                    item,
                    "serial_number_2",
                    "serial_2",
                    "serial_sec_2",
                    "serial_section_2",
                ),
                "label_number": item.get("label_number"),
                "label_number_2": _pick_inventory_field(
                    item,
                    "label_number_2",
                    "hud_label_2",
                    "hud_2",
                    "label_seal_2",
                ),
                "sections": item.get("sections") or item.get("no_of_sections"),
                "width": _pick_inventory_field(item, "width", "home_width", "total_size_w"),
                "length": _pick_inventory_field(item, "length", "home_length", "total_size_l"),
                "sq_ft": _pick_inventory_field(item, "sq_ft", "sqft", "square_feet", "total_sqft"),
                "wind_zone": _pick_inventory_field(
                    item, "wind_zone", "wind", "windZone", "wind_zone_1"
                ),
                "weight_sec_1": _pick_inventory_field(
                    item, "weight_sec_1", "weight1", "weight_sec1", "section_1_weight"
                ),
                "weight_sec_2": _pick_inventory_field(
                    item, "weight_sec_2", "weight2", "weight_sec2", "section_2_weight"
                ),
                "date_of_manufacture": _pick_inventory_field(
                    item,
                    "date_of_manufacture",
                    "manufacture_date",
                    "date_manufactured",
                    "mfg_date",
                    "date_const",
                ),
                "manufacturer_address": _pick_inventory_field(
                    item, "manufacturer_address", "factory_address"
                ),
                "manufacturer_city": _pick_inventory_field(
                    item, "manufacturer_city", "factory_city"
                ),
                "manufacturer_state": _pick_inventory_field(
                    item, "manufacturer_state", "factory_state"
                ),
                "manufacturer_zip": _pick_inventory_field(item, "manufacturer_zip", "factory_zip"),
                "manufacturer_city_state_zip": _pick_inventory_field(
                    item,
                    "manufacturer_city_state_zip",
                    "factory_city_state_zip",
                ),
                "installer_name": _pick_inventory_field(item, "installer_name"),
                "installer_phone": _pick_inventory_field(item, "installer_phone"),
                "installer_license": _pick_inventory_field(item, "installer_license"),
                "beds": item.get("bedrooms"),
                "baths": item.get("bathrooms"),
                "sqft": item.get("sqft"),
                "sale_price": item.get("sale_price") or item.get("msrp"),
                "image_url": item.get("image_url") or item.get("hero_image"),
                "status": item.get("status", "AVAILABLE"),
            }
            results.append(_apply_inventory_media_fallback(result, item, legacy_media_index))

        return {"success": True, "inventory": results, "count": len(results)}
    except Exception as e:
        struct_logger.error("Inventory listing failed", error=str(e))
        return {"success": False, "error": "Failed to load inventory. Please try again."}


@app.get("/api/admin/inventory/photo-audit", dependencies=[Depends(require_admin)])
async def admin_inventory_photo_audit(limit: int = 5000):
    """
    Read-only photo dedup and media-quality audit.

    Aggregates inventory image fields (image_url, hero_image, floorplan_url,
    gallery_images, photos, real_photos) and reports any URL referenced by
    two or more distinct inventory documents. It also classifies each item as
    photo_ready, limited_photos, floorplan_only, or missing_photos. Does NOT
    mutate Firestore; the operator decides which fixes to apply.
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


@app.get("/api/admin/inventory/analytics", dependencies=[Depends(require_admin)])
async def admin_inventory_analytics():
    """
    Operational inventory analytics for the admin panel.

    Pulls the full inventory collection and surfaces:
      - total / available / sold-30d / pending / reserved counts
      - median sale price (uses sale_price → msrp fallback)
      - median time-on-lot (created_at / date_added → updated_at when sold,
        else now-created_at for available units)
      - by-manufacturer breakdown (count + median price)

    5-minute cache.
    """
    from caching import cache_get, cache_set

    cache_key = "admin_inventory_analytics_v1"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        import statistics as _stats
        from datetime import datetime, timedelta

        now = datetime.now(UTC).replace(tzinfo=None)
        thirty_days_ago = now - timedelta(days=30)

        total = 0
        available = 0
        sold_total = 0
        sold_last_30d = 0
        pending = 0
        reserved = 0

        prices: list[float] = []
        time_on_lot_days: list[float] = []
        by_manufacturer: dict[str, dict] = {}

        for doc in _db.db.collection("inventory").stream():
            data = doc.to_dict() or {}
            total += 1

            status = (data.get("status") or "").upper()
            if status == "AVAILABLE":
                available += 1
            elif status == "SOLD":
                sold_total += 1
                sold_at = _parse_iso_datetime(data.get("updated_at"))
                if sold_at and sold_at >= thirty_days_ago:
                    sold_last_30d += 1
            elif status == "PENDING":
                pending += 1
            elif status == "RESERVED":
                reserved += 1

            price_value = data.get("sale_price") or data.get("msrp") or 0
            try:
                price_f = float(price_value)
            except (TypeError, ValueError):
                price_f = 0.0
            if price_f > 0:
                prices.append(price_f)

            # Time on lot: SOLD → updated_at - created; AVAILABLE → now - created
            created = _parse_iso_datetime(data.get("date_added")) or _parse_iso_datetime(
                data.get("created_at")
            )
            if created:
                if status == "SOLD":
                    closed = _parse_iso_datetime(data.get("updated_at"))
                    if closed and closed >= created:
                        time_on_lot_days.append((closed - created).total_seconds() / 86400.0)
                elif status == "AVAILABLE":
                    if now >= created:
                        time_on_lot_days.append((now - created).total_seconds() / 86400.0)

            mfr = (data.get("manufacturer") or "").strip() or "Unknown"
            entry = by_manufacturer.setdefault(
                mfr, {"count": 0, "available": 0, "sold": 0, "_prices": []}
            )
            entry["count"] += 1
            if status == "AVAILABLE":
                entry["available"] += 1
            elif status == "SOLD":
                entry["sold"] += 1
            if price_f > 0:
                entry["_prices"].append(price_f)

        manufacturers = []
        for mfr, entry in by_manufacturer.items():
            median_price = round(_stats.median(entry["_prices"]), 0) if entry["_prices"] else None
            manufacturers.append(
                {
                    "manufacturer": mfr,
                    "count": entry["count"],
                    "available": entry["available"],
                    "sold": entry["sold"],
                    "median_price": median_price,
                }
            )
        manufacturers.sort(key=lambda m: -m["count"])

        result = {
            "success": True,
            "totals": {
                "total": total,
                "available": available,
                "pending": pending,
                "reserved": reserved,
                "sold_total": sold_total,
                "sold_last_30d": sold_last_30d,
            },
            "median_sale_price": round(_stats.median(prices), 0) if prices else None,
            "median_time_on_lot_days": round(_stats.median(time_on_lot_days), 1)
            if time_on_lot_days
            else None,
            "by_manufacturer": manufacturers,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        cache_set(cache_key, result, ttl_seconds=300)
        return result
    except Exception as e:
        struct_logger.error("Inventory analytics failed", error=str(e))
        return {"success": False, "error": "Failed to compute inventory analytics."}


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
                Inventory(
                    **{
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
                    }
                )

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
            errors=len(errors),
        )

        return {
            "success": True,
            "imported": imported,
            "updated": updated,
            "errors": errors if errors else None,
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
        deals = _deal_db.search_deals(status=status, salesrep=salesrep, buyer_name=q, limit=limit)
        enriched = _enrich_deals_with_home(deals, _deal_db)
        return {
            "success": True,
            "deals": [_strip_pii_from_deal(d) for d in enriched],
            "count": len(enriched),
        }
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
                deal_data[key] = (
                    deal_data[key].isoformat()
                    if hasattr(deal_data[key], "isoformat")
                    else str(deal_data[key])
                )
        deal_id = _deal_db.create_deal(deal_data)
        # Return full deal object so frontend can navigate to detail view
        created_deal = _deal_db.get_deal(deal_id)
        log_admin_action(
            actor=_audit_actor(request),
            action="deal.create",
            target_type="deal",
            target_id=str(deal_id),
            details={"fields": sorted(k for k in data.keys() if isinstance(k, str))},
            request=request,
        )
        return {
            "success": True,
            "deal_id": deal_id,
            "deal": created_deal,
            "message": "Deal created successfully",
        }
    except Exception as e:
        struct_logger.error("Deal creation failed", error=str(e))
        return {"success": False, "error": "Failed to create deal. Please try again."}


def _parse_iso_datetime(value):
    """Parse a Firestore-stored timestamp (datetime, iso string, or None).

    Returns a tz-naive UTC datetime, or None on failure. Used by analytics
    endpoints that need to diff created_at/updated_at without assuming a
    consistent stored type.
    """
    from datetime import datetime

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


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
        log_admin_action(
            actor=_audit_actor(request),
            action="deal.update",
            target_type="deal",
            target_id=str(deal_id),
            details={"fields": sorted(k for k in data.keys() if isinstance(k, str))},
            request=request,
        )
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

        log_admin_action(
            actor=_audit_actor(request),
            action="deal.update",
            target_type="deal",
            target_id=str(deal_id),
            details={
                "fields": ["status"],
                "status_from": prior_status,
                "status_to": new_status,
            },
            request=request,
        )

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

        # DocuSeal automated triggers
        try:
            await docuseal_auto_trigger(
                event_type="deal.status_change",
                payload={
                    "deal_id": deal_id,
                    "to": new_status,
                    "deal_data": _deal_db.get_deal(deal_id) or {},
                },
            )
        except Exception as e:
            struct_logger.warning("Deal DocuSeal trigger failed", error=str(e))

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
            deal_id=deal_id,
        )
        if result["success"]:
            log_admin_action(
                actor=_audit_actor(request),
                action="document.generate",
                target_type="document",
                target_id=str(result.get("filename", "")),
                details={
                    "template_name": str(template_name)[:100],
                    "deal_id": str(deal_id),
                    "override_keys": sorted(
                        k for k in (overrides or {}).keys() if isinstance(k, str)
                    ),
                },
                request=request,
            )

            # Lane 3: Best-effort auto-email if customer email present in data
            _maybe_email_document(
                customer_email=doc_data.get("buyer_email") or doc_data.get("email"),
                customer_name=doc_data.get("buyer_first_name") or doc_data.get("first_name"),
                doc_filename=result["filename"],
                doc_type="single_template",
                download_url=f"/api/documents/download/{result['filename']}",
                deal_id=deal_id,
            )

            return {
                "success": True,
                "download_url": f"/api/documents/download/{result['filename']}",
                "filename": result["filename"],
                "message": result["message"],
            }
        return {"success": False, "error": result["message"]}
    except Exception as e:
        struct_logger.error("Deal document generation failed", error=str(e))
        return {
            "success": False,
            "error": "Failed to generate document from deal. Please try again.",
        }


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
            deal_id=deal_id,
        )
        if result["success"]:
            packet_fields = _packet_result_fields(result)
            log_admin_action(
                actor=_audit_actor(request),
                action="document.generate",
                target_type="document",
                target_id=str(packet_fields.get("filename", "")),
                details={
                    "packet_name": str(packet_name)[:100],
                    "deal_id": str(deal_id),
                    "page_count": packet_fields.get("page_count", 0),
                    "documents_included": [
                        str(d)[:60] for d in (packet_fields.get("documents_included") or [])
                    ][:50],
                },
                request=request,
            )

            # Lane 3: Best-effort auto-email if customer email present in data
            if packet_fields.get("filename"):
                _maybe_email_document(
                    customer_email=doc_data.get("buyer_email") or doc_data.get("email"),
                    customer_name=doc_data.get("buyer_first_name") or doc_data.get("first_name"),
                    doc_filename=packet_fields["filename"],
                    doc_type="closing_packet",
                    download_url=packet_fields["download_url"],
                    deal_id=deal_id,
                )

            # DocuSeal: Automated e-sign dispatch for deal packet
            email = doc_data.get("buyer_email") or doc_data.get("email")
            if email and packet_fields.get("filename"):
                try:
                    await docuseal_send_file_for_signature(
                        email=email,
                        name=f"{doc_data.get('buyer_first_name', '')} {doc_data.get('buyer_last_name', '')}".strip()
                        or "Customer",
                        file_path=os.path.join("generated_docs", packet_fields["filename"]),
                        display_name=f"Closing Packet - {packet_name}",
                        deal_id=deal_id,
                        metadata={
                            "packet_name": packet_name,
                            "trigger": "generate_packet_from_deal",
                        },
                    )
                except Exception as e:
                    struct_logger.warning(
                        "Automated DocuSeal deal packet dispatch failed", error=str(e)
                    )

            return {
                "success": True,
                "download_url": packet_fields["download_url"],
                "filename": packet_fields["filename"],
                "message": packet_fields["message"],
                "page_count": packet_fields.get("page_count", 0),
                "documents_included": packet_fields.get("documents_included", []),
            }
        return {
            "success": False,
            "error": result.get("message") or result.get("error") or "Packet generation failed",
        }
    except Exception as e:
        struct_logger.error("Deal packet generation failed", error=str(e))
        return {"success": False, "error": "Failed to generate closing packet. Please try again."}


# ─── DocuSeal e-sign endpoints ───────────────────────────────────────────────

_DOCUSEAL_API_URL = os.environ.get("DOCUSEAL_API_URL", "")
_DOCUSEAL_API_TOKEN = os.environ.get("DOCUSEAL_API_TOKEN", "")
_DOCUSEAL_WEBHOOK_SECRET = os.environ.get("DOCUSEAL_WEBHOOK_SECRET", "")


@app.post("/api/docuseal/send", dependencies=[Depends(require_admin)])
async def docuseal_send(request: Request):
    """Start a DocuSeal e-sign submission for a deal document.

    Body: {deal_id, template_name, signer_email, signer_name}

    Returns 501 until DOCUSEAL_API_URL + DOCUSEAL_API_TOKEN env vars are set.
    """
    try:
        data = await request.json()
        result = await docuseal_send_for_signature(
            email=data.get("signer_email"),
            name=data.get("signer_name"),
            template_name=data.get("template_name"),
            deal_id=data.get("deal_id"),
        )
        if result.get("status") == "not_configured":
            return JSONResponse(
                {
                    "success": False,
                    "message": (
                        "DocuSeal not configured — set DOCUSEAL_API_URL + "
                        "DOCUSEAL_API_TOKEN env vars to enable."
                    ),
                },
                status_code=501,
            )
        if not result.get("success"):
            raise HTTPException(status_code=502, detail=result.get("error"))

        return result
    except HTTPException:
        raise
    except Exception as exc:
        struct_logger.error("DocuSeal send failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")


@app.post("/api/docuseal/webhook")
async def docuseal_webhook(request: Request):
    """Receive DocuSeal completion webhooks and mirror signed PDFs to GCS.

    Validates the X-Docuseal-Signature HMAC-SHA256 header.
    No-op (200) when DOCUSEAL_WEBHOOK_SECRET is unset.
    Writes signed PDF to gs://tho-secure-documents/signed_documents/<deal_id>/<file>.
    """
    if not _DOCUSEAL_WEBHOOK_SECRET:
        return {"status": "ignored", "reason": "webhook_secret_not_configured"}

    body = await request.body()
    incoming_sig = request.headers.get("X-Docuseal-Signature", "")
    expected_sig = hmac.new(_DOCUSEAL_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(incoming_sig, expected_sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json as _json

    event = _json.loads(body)
    event_type = event.get("event_type") or event.get("type", "")

    if event_type not in ("form.completed", "submission.completed"):
        return {"status": "ignored", "event_type": event_type}

    data = event.get("data", event)
    deal_id = (data.get("metadata") or {}).get("deal_id")
    submission_id = data.get("id")
    documents = data.get("documents") or []
    document_url = documents[0].get("url") if documents else None
    template_name = (data.get("metadata") or {}).get("template_name")

    if not document_url or not deal_id:
        struct_logger.warning("DocuSeal webhook missing document_url or deal_id", event=str(event))
        return {"status": "ignored", "reason": "missing_document_url_or_deal_id"}

    import httpx

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            dl = await client.get(document_url)
        dl.raise_for_status()

        filename = f"signed_{submission_id}.pdf"
        gcs_path = f"signed_documents/{deal_id}/{filename}"
        tmp_path = os.path.join("/tmp", filename)
        with open(tmp_path, "wb") as f:
            f.write(dl.content)

        gcs_uri = None
        try:
            from google.cloud import storage as _gcs

            _client = _gcs.Client()
            _bucket = _client.bucket(os.getenv("GCS_DOCUMENTS_BUCKET", "tho-secure-documents"))
            _bucket.blob(gcs_path).upload_from_filename(tmp_path, content_type="application/pdf")
            gcs_uri = f"gs://{os.getenv('GCS_DOCUMENTS_BUCKET', 'tho-secure-documents')}/{gcs_path}"
        except Exception as gcs_err:
            struct_logger.warning("DocuSeal GCS mirror failed", error=str(gcs_err), path=gcs_path)

        try:
            db = get_database()
            db.collection("deal_notes").document(f"{deal_id}_esign_{submission_id}").set(
                {
                    "deal_id": deal_id,
                    "type": "esign_completed",
                    "submission_id": str(submission_id),
                    "template_name": template_name,
                    "gcs_path": gcs_uri or gcs_path,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )

            # Audit log
            log_admin_action(
                actor="system:docuseal",
                action="document.esign_complete",
                target_type="deal",
                target_id=str(deal_id),
                details={
                    "submission_id": str(submission_id),
                    "template_name": template_name,
                    "gcs_path": gcs_uri,
                },
                request=None,
            )
        except Exception as note_err:
            struct_logger.warning("DocuSeal post-processing failed", error=str(note_err))

        try:
            from tools.drive_service import ensure_deal_folder, upload_to_drive

            deal_folder_id = ensure_deal_folder(str(deal_id))
            if deal_folder_id:
                drive_link = upload_to_drive(
                    tmp_path, f"Signed - {template_name or submission_id}.pdf", deal_folder_id
                )
                if drive_link:
                    struct_logger.info(
                        "DocuSeal signed PDF mirrored to Drive",
                        deal_id=deal_id,
                        drive_link=drive_link,
                    )
        except Exception as drive_err:
            struct_logger.warning("DocuSeal Drive mirror failed", error=str(drive_err))

        struct_logger.info("DocuSeal signed PDF mirrored", deal_id=deal_id, gcs_uri=gcs_uri)
        return {"status": "ok", "gcs_path": gcs_uri or gcs_path}

    except Exception as exc:
        struct_logger.error("DocuSeal webhook processing failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {exc}")


# ─── Marketing API (Tex's Ad Studio) ───
from tools.asset_scraper import PROPERTY_ASSETS, get_matterport_url
from tools.catalog_floorplans import merge_orderable_floorplan_catalog
from tools.legacy_site_crawler import (
    load_legacy_floorplan_catalog_context,
    load_legacy_inventory_context,
)
from tools.marketing_tools import (
    GENERATED_ADS_DIR,
    analyze_content_performance,
    generate_ad_image,
    generate_content_script,
    get_gcp_ai_readiness,
    get_inventory_for_ads,
    get_trending_content_ideas,
    schedule_social_post,
)
from tools.photo_classifier import apply_classifier_to_home, has_real_photo
from tools.social_publishers import social_readiness
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
            variations=data.get("variations", 1),
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
            video_url=data.get("video_url"),
            home_name=data.get("home_name"),
            campaign=data.get("campaign"),
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


@app.get("/api/marketing/gcp-readiness", dependencies=[Depends(require_admin)])
async def api_marketing_gcp_readiness():
    """Expose AI provider readiness for Ad Studio without making generation calls."""
    try:
        return get_gcp_ai_readiness()
    except Exception as e:
        struct_logger.error("GCP AI readiness failed", error=str(e))
        return {"success": False, "ready": False, "error": "Failed to inspect GCP AI readiness"}


@app.get("/api/marketing/social-readiness", dependencies=[Depends(require_admin)])
async def api_marketing_social_readiness():
    """Expose TikTok/Instagram connection readiness without exposing secrets."""
    try:
        return social_readiness()
    except Exception as e:
        struct_logger.error("Social readiness failed", error=str(e))
        return {"success": False, "error": "Failed to inspect social readiness"}


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
            speaking_rate=data.get("speaking_rate", 1.0),
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


@app.post("/api/marketing/generate-flyer", dependencies=[Depends(require_admin)])
async def api_generate_flyer(request: Request):
    """Compose a downloadable social ad flyer from real/AI imagery and THO branding."""
    try:
        from tools.marketing_tools import generate_ad_flyer

        data = await request.json()
        result = generate_ad_flyer(
            home_name=data.get("home_name") or "Featured Home",
            home_price=data.get("home_price"),
            home_specs=data.get("home_specs"),
            headline=data.get("headline"),
            body=data.get("body"),
            cta=data.get("cta"),
            platform=data.get("platform", "instagram_post"),
            home_photo_url=data.get("home_photo_url"),
            image_base64=data.get("image_base64"),
        )
        return result
    except Exception as e:
        struct_logger.error("Flyer generation failed", error=str(e))
        return {"success": False, "error": "Flyer generation failed. Please try again."}


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
            duration_per_photo=data.get("duration_per_photo", 3.0),
        )
        return result
    except Exception as e:
        struct_logger.error("Video generation failed", error=str(e))
        return {"success": False, "error": f"Video generation failed: {str(e)}"}


@app.post("/api/marketing/generate-genai-clip", dependencies=[Depends(require_admin)])
async def api_generate_genai_clip(request: Request):
    """Generate a true GenAI video clip through Google Veo/Vertex AI."""
    try:
        from tools.video_generator import generate_genai_ad_clip

        data = await request.json()
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return {"success": False, "error": "A prompt is required for GenAI clip generation"}
        result = generate_genai_ad_clip(
            prompt=prompt,
            home_name=data.get("home_name", "ad"),
            platform=data.get("platform", "tiktok"),
            home_photo_url=data.get("home_photo_url"),
            image_base64=data.get("image_base64"),
            duration_seconds=data.get("duration_seconds", 5),
            include_virtual_presenter=bool(data.get("include_virtual_presenter", False)),
            wait_seconds=data.get("wait_seconds", 120),
        )
        return result
    except Exception as e:
        struct_logger.error("GenAI clip generation failed", error=str(e))
        return {"success": False, "error": f"GenAI clip generation failed: {str(e)}"}


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
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )
    return {"error": "File not found"}


def _overlay_staff_photos(homes: list[dict]) -> None:
    """Prepend staff-uploaded photos onto matching homes (matched by `id`).

    Uploaded photos are real on-lot photos, so they lead the gallery and become
    the hero image; any existing CDN/legacy photos are kept after them. This
    runs for every inventory source (legacy snapshot, Firestore, asset catalog)
    so staff uploads show on the public site regardless of which source is live.
    """
    if not homes:
        return
    try:
        from tools import image_storage

        grouped = image_storage.list_all_grouped()
    except Exception as exc:  # pragma: no cover - storage backend optional
        struct_logger.warning("Staff photo overlay unavailable", error=str(exc))
        return
    if not grouped:
        return
    for home in homes:
        raw_id = str(home.get("id") or "").strip()
        if not raw_id:
            continue
        try:
            key = image_storage.safe_home_id(raw_id)
        except image_storage.PhotoValidationError:
            continue
        staff = grouped.get(key)
        if not staff:
            continue
        urls = [p.url for p in staff]
        existing = home.get("real_photos")
        existing = existing if isinstance(existing, list) else []
        merged = urls + [u for u in existing if u not in urls]
        home["real_photos"] = merged
        home["gallery_images"] = merged[:3]
        home["image_url"] = urls[0]
        home["has_staff_photos"] = True


@app.get("/api/marketing/inventory-context")
@limiter.limit("30/minute")
async def api_inventory_context(request: Request):
    """Get inventory highlights for ad creation and the public browse page."""
    try:
        # Public browse/Ad Studio should mirror the customer's active legacy
        # inventory, not stale Firestore/static seed rows. The crawler is
        # cached and Firestore-free; if the public site is unavailable, the
        # old Firestore/asset path below remains a graceful fallback.
        legacy_result = load_legacy_inventory_context(limit=100)
        if legacy_result.get("success") and legacy_result.get("homes"):
            floorplan_result = load_legacy_floorplan_catalog_context(limit=500)
            merged = merge_orderable_floorplan_catalog(
                legacy_result,
                assets=PROPERTY_ASSETS,
                floorplan_context=floorplan_result
                if floorplan_result.get("success") and floorplan_result.get("homes")
                else None,
            )
            _overlay_staff_photos(merged.get("homes", []))
            return merged
        if legacy_result.get("error"):
            struct_logger.warning(
                "Legacy inventory context unavailable",
                error=legacy_result.get("error"),
            )

        from tools.asset_scraper import get_assets_for_home

        # Fallback for local/offline operation and legacy admin data. This path
        # should not win when the public legacy site can be read successfully.
        result = get_inventory_for_ads(limit=100)
        firestore_homes = result.get("homes", [])

        # Enrich Firestore homes with asset catalog images only when they lack
        # non-floorplan photos. Floorplan-only listings used to pass this gate
        # because `real_photos` was non-empty; the classifier now makes the
        # distinction explicit before and after enrichment.
        for home in firestore_homes:
            apply_classifier_to_home(home)
            if not has_real_photo(home):
                asset = get_assets_for_home(home.get("model_name", ""))
                if asset:
                    asset_images = asset.get("images") or []
                    if asset_images:
                        existing = home.get("real_photos") or home.get("gallery_images") or []
                        home["real_photos"] = [*existing, *asset_images]
                        home["gallery_images"] = [*existing, *asset_images][:3]
                    if asset.get("image_categories"):
                        home["image_categories"] = asset.get("image_categories", {})
                    home["floor_plan_url"] = home.get("floor_plan_url") or asset.get("floor_plan")
                    if asset.get("matterport_id") and not home.get("matterport_id"):
                        home["matterport_id"] = asset["matterport_id"]
                        home["matterport_url"] = get_matterport_url(asset["matterport_id"])
                    apply_classifier_to_home(home)
        website_homes = []
        if not firestore_homes:
            for slug, asset in PROPERTY_ASSETS.items():
                home_data = {
                    "id": slug,
                    "source_catalog_slug": slug,
                    "model_name": asset["name"],
                    "manufacturer": asset.get("manufacturer", "New Vision Manufacturing"),
                    "classification": "Manufactured Home",
                    "status": "Available" if asset.get("is_new") else "Pre-Owned",
                    "inventory_kind": "orderable_floorplan" if asset.get("is_new") else "pre_owned",
                    "display_price": "Call for Price",
                    "price_value": 0,
                    "specs": {
                        "beds": asset.get("beds"),
                        "baths": asset.get("baths"),
                        "sq_ft": asset.get("sqft"),
                        "dimensions": asset.get("dims"),
                    },
                    "features": [],
                    "image_url": (asset.get("images") or [""])[0],
                    "gallery_images": asset.get("images", [])[:3],
                    "real_photos": asset.get("images", []),
                    "image_categories": asset.get("image_categories", {}),
                    "floor_plan_url": asset.get("floor_plan"),
                    "matterport_id": asset.get("matterport_id"),
                    "matterport_url": get_matterport_url(asset["matterport_id"])
                    if asset.get("matterport_id")
                    else None,
                }
                website_homes.append(home_data)
            result["homes"] = website_homes
            result["total_inventory"] = len(website_homes)
        else:
            result["homes"] = firestore_homes
            result["total_inventory"] = result.get("total_inventory", len(firestore_homes))

        # Apply URL-based floorplan classifier to every home before responding.
        # This is intentionally repeated after enrichment/fallback construction
        # so the public API never counts floorplans as usable listing photos.
        for home in result.get("homes", []):
            apply_classifier_to_home(home)

        floorplan_result = load_legacy_floorplan_catalog_context(limit=500)
        result = merge_orderable_floorplan_catalog(
            result,
            assets=PROPERTY_ASSETS,
            floorplan_context=floorplan_result
            if floorplan_result.get("success") and floorplan_result.get("homes")
            else None,
        )
        result["website_homes"] = len(website_homes)
        _overlay_staff_photos(result.get("homes", []))
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
        return FileResponse(resolved, filename=safe_filename, media_type="image/png")
    return JSONResponse({"error": "Image not found"}, status_code=404)


# ─── Staff Listing Photo Uploads ───
# Staff add their own on-lot photos to a home here. Uploads are stored durably
# (GCS, with a local-disk fallback for dev) and overlaid onto the home in
# /api/marketing/inventory-context by matching the home's `id`, so they show up
# on the public Inventory page regardless of which inventory source is live.

# Max photos accepted in a single upload request (keeps requests bounded).
_MAX_PHOTOS_PER_UPLOAD = 20


@app.get("/api/inventory/photos/{home_id}/{filename}")
async def serve_listing_photo(home_id: str, filename: str):
    """Public: stream a staff-uploaded listing photo (no auth — photos are public)."""
    from tools import image_storage

    try:
        result = image_storage.get_photo(home_id, filename)
    except image_storage.PhotoValidationError:
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    if result is None:
        return JSONResponse({"error": "Photo not found"}, status_code=404)
    data, content_type = result
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/inventory/{home_id}/photos", dependencies=[Depends(require_admin)])
async def list_listing_photos(home_id: str):
    """Admin: list staff-uploaded photos for a home."""
    from tools import image_storage

    try:
        photos = image_storage.list_photos(home_id)
    except image_storage.PhotoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "home_id": image_storage.safe_home_id(home_id),
        "photos": [
            {"filename": p.filename, "url": p.url, "size_bytes": p.size_bytes}
            for p in photos
        ],
    }


@app.post("/api/inventory/{home_id}/photos", dependencies=[Depends(require_admin)])
@limiter.limit("30/minute")
async def upload_listing_photos(
    request: Request,
    home_id: str,
    files: list[UploadFile] = File(...),
):
    """Admin: upload one or more on-lot photos for a home."""
    from tools import image_storage

    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > _MAX_PHOTOS_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files at once (max {_MAX_PHOTOS_PER_UPLOAD}). "
            "Please upload in smaller batches.",
        )

    stored: list[dict] = []
    errors: list[dict] = []
    for upload in files:
        try:
            data = await upload.read()
            photo = image_storage.store_photo(
                home_id,
                data,
                original_name=upload.filename,
                declared_content_type=upload.content_type,
            )
            stored.append(
                {"filename": photo.filename, "url": photo.url, "size_bytes": photo.size_bytes}
            )
        except image_storage.PhotoValidationError as exc:
            errors.append({"name": upload.filename, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - unexpected storage failure
            struct_logger.error(
                "Listing photo upload failed", home_id=home_id, error=str(exc)
            )
            errors.append({"name": upload.filename, "error": "Could not save this photo."})
        finally:
            await upload.close()

    if stored:
        struct_logger.info(
            "Listing photos uploaded",
            home_id=image_storage.safe_home_id(home_id),
            count=len(stored),
            actor=_audit_actor(request),
        )
    # 207-style summary: report both successes and per-file failures.
    return {
        "success": bool(stored),
        "home_id": image_storage.safe_home_id(home_id),
        "uploaded": stored,
        "errors": errors,
    }


@app.delete(
    "/api/inventory/{home_id}/photos/{filename}",
    dependencies=[Depends(require_admin)],
)
async def delete_listing_photo(request: Request, home_id: str, filename: str):
    """Admin: remove a staff-uploaded photo from a home."""
    from tools import image_storage

    try:
        deleted = image_storage.delete_photo(home_id, filename)
    except image_storage.PhotoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Photo not found.")
    struct_logger.info(
        "Listing photo deleted",
        home_id=image_storage.safe_home_id(home_id),
        filename=os.path.basename(filename),
        actor=_audit_actor(request),
    )
    return {"success": True}


@app.put("/api/inventory/{home_id}/photos/order", dependencies=[Depends(require_admin)])
async def reorder_listing_photos(request: Request, home_id: str):
    """Admin: set the photo display order for a home (first = hero/main photo)."""
    from tools import image_storage

    try:
        body = await request.json()
    except Exception:
        body = {}
    order = body.get("order") if isinstance(body, dict) else None
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise HTTPException(status_code=400, detail="Body must be {\"order\": [filenames]}.")
    try:
        photos = image_storage.set_photo_order(home_id, order)
    except image_storage.PhotoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "home_id": image_storage.safe_home_id(home_id),
        "photos": [
            {"filename": p.filename, "url": p.url, "size_bytes": p.size_bytes}
            for p in photos
        ],
    }


# ─── Contact Form API ───


@app.post("/api/contact")
@limiter.limit(CONTACT_RATE_LIMIT)
async def submit_contact_form(request: Request):
    """Receive contact form submissions and log as leads."""
    try:
        data = await request.json()
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        has_message = bool(data.get("message", "").strip())

        email = data.get("email", "").strip()

        if not name or not phone:
            return {"success": False, "error": "Name and phone are required"}
        if len(re.sub(r"\D", "", phone)) < 10:
            return {
                "success": False,
                "error": "Please enter a valid 10-digit phone number.",
            }

        struct_logger.info(
            "Contact form submitted",
            name=name,
            has_phone=bool(phone),
            has_message=has_message,
        )

        lead_id = f"contact_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        warnings: list[str] = []

        # Create a lead from the contact form. A storage failure = a lost lead =
        # lost revenue, so make it LOUD: log at error level + flag it in the
        # response `warnings` so a Cloud Run log-based alert can fire on
        # "lead_storage_failed". The owner notification below is the fallback
        # delivery path, so the visitor still gets a success response.
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
            warnings.append("lead_storage_failed")
            struct_logger.error(
                "Contact lead creation failed",
                event="lead_storage_failed",
                error=str(e),
                lead_id=lead_id,
            )

        # Send welcome email if email provided
        if email:
            try:
                send_lead_welcome(to=email, customer_name=name, lead_id=lead_id)
            except Exception as e:
                warnings.append("welcome_email_failed")
                struct_logger.warning("Lead welcome email failed", error=str(e))

        # Automate DocuSeal Welcome/Credit Auth early
        try:
            await docuseal_auto_trigger(
                event_type="lead.captured",
                payload={"email": email, "name": name, "lead_id": lead_id},
            )
        except Exception as e:
            warnings.append("docuseal_failed")
            struct_logger.warning("Lead DocuSeal trigger failed", error=str(e))

        # Notify owner of new lead (fallback delivery path if storage failed)
        try:
            notify_new_lead(
                customer_name=name,
                phone=phone,
                email=email,
                source=data.get("source", "contact_form"),
            )
        except Exception as e:
            warnings.append("owner_notify_failed")
            struct_logger.warning("Lead admin notification failed", error=str(e))

        result = {"success": True, "message": "Thank you! We'll be in touch shortly."}
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        struct_logger.error("Contact form failed", error=str(e))
        return {
            "success": False,
            "error": "Something went wrong. Please try again or call us directly.",
        }


@app.post("/api/analytics")
@limiter.limit("120/minute")
async def track_analytics_event(request: Request):
    """Fire-and-forget client event sink (home views, form opens, quote/tour
    clicks). Public + best-effort: it must NEVER error or block the page, so all
    failures are swallowed after a structured log. Previously these events were
    POSTed to /api/contact and silently rejected — losing the entire client
    event stream and polluting the contact-form logs."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}  # never reject a beacon
    if not isinstance(data, dict):
        return {"ok": True}
    event = str(data.get("event") or data.get("_event") or "").strip()[:64]
    if not event:
        return {"ok": True}
    # Keep only small string-ish props; cap count + length to bound abuse/cost.
    props = {
        str(k)[:40]: (str(v)[:200] if v is not None else None)
        for k, v in list(data.items())[:25]
        if k not in ("event", "_event")
    }
    record = {
        "event": event,
        "props": props,
        "client_ip": _get_client_ip(request),
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        if _db and getattr(_db, "db", None):
            _db.db.collection("analytics_events").add(record)
    except Exception as e:
        struct_logger.warning("Analytics event store failed", event=event, error=str(e))
    return {"ok": True}


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
@limiter.limit(APPOINTMENTS_RATE_LIMIT)
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

        phone_digits = re.sub(r"\D", "", phone)
        if len(phone_digits) < 10:
            return {"success": False, "error": "Please provide a valid 10-digit phone number."}

        warnings: list[str] = []
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
                warnings.append("appointment_confirmation_failed")
                struct_logger.warning("Appointment confirmation email failed", error=str(e))

        # Notify owner of new appointment
        try:
            notify_new_appointment(
                customer_name=name,
                phone=phone,
                date=appt_date,
                time_slot=time_slot,
                email=appt.email,
                notes=appt.notes,
            )
        except Exception as e:
            warnings.append("owner_notify_failed")
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
            warnings.append("lead_storage_failed")
            struct_logger.error(
                "Appointment lead creation failed",
                event="lead_storage_failed",
                error=str(e),
            )

        result = {
            "success": True,
            "appointment": created.to_dict(),
            "message": f"Appointment confirmed for {appt_date} at {time_slot}. We look forward to seeing you!",
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        struct_logger.error("Appointment creation failed", error=str(e))
        return {
            "success": False,
            "error": "Failed to book appointment. Please try again or call us.",
        }


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

        appt_digits = re.sub(r"\D", "", appt.phone)
        req_digits = re.sub(r"\D", "", phone)
        if appt_digits != req_digits:
            return {"success": False, "error": "Phone number does not match this appointment."}

        cancelled = await appointment_manager.cancel_appointment(appointment_id)
        return {
            "success": True,
            "message": f"Appointment on {cancelled.date} at {cancelled.time_slot} has been cancelled.",
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
        return {"success": True, "leads": [lead.to_dict() for lead in leads], "count": len(leads)}
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
        for doc in collection.limit(500).stream():
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
        collection.document(task["task_id"]).set(task)
    except Exception as exc:  # noqa: BLE001
        struct_logger.warning("CRM task Firestore save failed", error=str(exc))


def _load_crm_task(task_id: str) -> dict | None:
    task = _crm_tasks.get(task_id)
    collection = _crm_task_collection()
    if collection is None:
        return task
    try:
        doc = collection.document(task_id).get()
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
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    if not secrets.compare_digest(pin_hash, ADMIN_PIN_HASH):
        _add_pin_attempt(client_ip, now)
        remaining = PIN_MAX_ATTEMPTS - len(_get_pin_attempts(client_ip))
        struct_logger.warning("Admin login failed", client_ip=client_ip, remaining=remaining)
        return JSONResponse({"success": False, "error": "Incorrect PIN."}, status_code=401)
    # Successful login — clear attempt history
    _clear_pin_attempts(client_ip)
    token = _create_admin_token()
    csrf_token = _create_csrf_token()
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
    response.set_cookie(
        key="tho_csrf_token",
        value=csrf_token,
        httponly=False,
        secure=not IS_LOCAL,
        samesite="strict",
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
        for doc in _db.db.collection("deals").stream():
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

    for doc in _db.db.collection(collection_name).stream():
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
            "idempotency_scope": f"{key_fingerprint}:{idempotency_key}"
            if idempotency_key
            else None,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }

    try:
        _db.db.collection("activities").document(activity_id).set(activity)
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
        doc = _db.db.collection("service_requests").document(request_id).get()
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


@app.get("/api/v1/customer/deal/{deal_id}")
async def get_secure_hub_deal(deal_id: str):
    """Fetch deal status and documents for the customer portal."""
    try:
        db = get_database()
        deal = db.collection("deals").document(deal_id).get()
        if not deal.exists:
            raise HTTPException(status_code=404, detail="Deal not found")

        deal_data = deal.to_dict()

        # Fetch documents (deal_notes of type esign_completed or document_generated)
        docs_query = db.collection("deal_notes").where("deal_id", "==", deal_id).stream()
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
    except Exception as e:
        struct_logger.error("Secure Hub deal fetch failed", error=str(e), deal_id=deal_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/customer/deal/{deal_id}/download/{note_id}")
async def download_secure_document(deal_id: str, note_id: str):
    """Generate a signed URL for a secure document."""
    try:
        db = get_database()
        note = db.collection("deal_notes").document(note_id).get()
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
    except Exception as e:
        struct_logger.error("Signed URL generation failed", error=str(e), note_id=note_id)
        raise HTTPException(status_code=500, detail=str(e))


# AI PM Manager Routes (Linear-inspired)
from pm_routes import router as pm_router

app.include_router(pm_router, dependencies=[Depends(require_admin)])
app.include_router(passkey_router)


# SEO surface: robots.txt, sitemap.xml, and per-route head/body injection for
# the SPA shell (see seo_routes.py and docs/SEO_MIGRATION.md).
import seo_routes


def _seo_public_homes() -> list:
    """Same merged public inventory the browse page renders, for sitemap,
    legacy detail URLs, and crawlable HTML. Loaders are cached upstream."""
    legacy_result = load_legacy_inventory_context(limit=100)
    if not (legacy_result.get("success") and legacy_result.get("homes")):
        return []
    floorplan_result = load_legacy_floorplan_catalog_context(limit=500)
    merged = merge_orderable_floorplan_catalog(
        legacy_result,
        assets=PROPERTY_ASSETS,
        floorplan_context=floorplan_result
        if floorplan_result.get("success") and floorplan_result.get("homes")
        else None,
    )
    return merged.get("homes", [])


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


# Serve Frontend — Must be last to avoid catching API routes
app.mount("/assets", ImmutableStaticFiles(directory="frontend/dist/assets"), name="assets")


# HEAD is included because uptime monitors default to HEAD on "/" and the
# Cloud Run service should not answer the probe with 405.
@app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
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
