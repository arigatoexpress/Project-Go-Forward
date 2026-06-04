"""
DocuSeal E-Signature Service for Texas Home Outlet.

Thin, config-gated client for DocuSeal (open-source DocuSign alternative). It
captures buyer/seller e-signatures on the closing packet (TMHA sales contract,
deposit agreement, warranties, disclosures, and the TDHCA 1023). Legal basis and
architecture: docs/TX_MH_COMPLIANCE_RESEARCH.md and docs/DOCUSEAL_INTEGRATION_SPEC.md.

The module degrades gracefully when DocuSeal is not configured — every public
function returns a structured ``{"success": False, "error": ...}`` and never
raises — mirroring email_service.py / GCS / Redis. It activates once the env
vars below are set, so it is safe to merge and deploy before DocuSeal exists.

Setup:
  1. Deploy DocuSeal (self-host recommended) or use the cloud, get an API token.
  2. Set env vars:
        DOCUSEAL_BASE_URL=https://sign.texashomeoutlet.com/api   (or https://api.docuseal.com)
        DOCUSEAL_API_TOKEN=xxxxx
        DOCUSEAL_WEBHOOK_SECRET=<random-string>   # set the same value as a
                                                  # custom header on DocuSeal's webhook
"""

from __future__ import annotations

import base64
import hmac
import logging
import os

import requests

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────
# Trailing slash is stripped so callers can join paths cleanly.
DOCUSEAL_BASE_URL = os.environ.get("DOCUSEAL_BASE_URL", "https://api.docuseal.com").rstrip("/")
DOCUSEAL_API_TOKEN = os.environ.get("DOCUSEAL_API_TOKEN", "")
# Shared secret echoed by DocuSeal on webhook calls (configure it as a custom
# header on the webhook in DocuSeal). The webhook handler ALSO re-fetches the
# submission via the authenticated API, so a leaked/absent secret cannot forge a
# completion — this is defense-in-depth, not the only guard.
DOCUSEAL_WEBHOOK_SECRET = os.environ.get("DOCUSEAL_WEBHOOK_SECRET", "")
# Header DocuSeal sends the shared secret in (configurable to match the dashboard).
DOCUSEAL_WEBHOOK_HEADER = os.environ.get("DOCUSEAL_WEBHOOK_HEADER", "X-Docuseal-Secret")

_TIMEOUT = 30  # seconds; DocuSeal calls upload PDFs, so allow headroom.


def is_configured() -> bool:
    """True when a base URL and API token are present."""
    return bool(DOCUSEAL_BASE_URL and DOCUSEAL_API_TOKEN)


def _not_configured() -> dict:
    logger.warning("DocuSeal not configured — set DOCUSEAL_BASE_URL and DOCUSEAL_API_TOKEN")
    return {
        "success": False,
        "error": "E-signature service not configured. Set DOCUSEAL_BASE_URL and DOCUSEAL_API_TOKEN.",
    }


def _headers() -> dict:
    return {"X-Auth-Token": DOCUSEAL_API_TOKEN, "Content-Type": "application/json"}


def create_submission_from_pdf(
    name: str,
    pdf_bytes: bytes,
    submitters: list[dict],
    *,
    send_email: bool = True,
) -> dict:
    """Create a DocuSeal submission directly from a generated PDF packet.

    ``submitters`` is a list of ``{"role": ..., "email": ..., "name": ...,
    "fields": [...]}`` dicts. Returns ``{"success": True, "submission_id": int,
    "submitters": [...]}`` (each submitter carries its signing ``slug``/url) on
    success, or a structured error.
    """
    if not is_configured():
        return _not_configured()
    if not pdf_bytes:
        return {"success": False, "error": "No document provided for signature."}
    if not submitters:
        return {"success": False, "error": "At least one signer is required."}

    payload = {
        "name": name,
        "send_email": send_email,
        "documents": [
            {
                "name": name,
                "file": base64.b64encode(pdf_bytes).decode("ascii"),
            }
        ],
        "submitters": submitters,
    }
    try:
        resp = requests.post(
            f"{DOCUSEAL_BASE_URL}/submissions/pdf",
            json=payload,
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("DocuSeal create submission failed: %s", e)
        return {"success": False, "error": "Could not reach the e-signature service."}

    if resp.status_code >= 400:
        logger.error("DocuSeal create submission %s: %s", resp.status_code, resp.text[:300])
        return {"success": False, "error": f"E-signature service error ({resp.status_code})."}

    data = resp.json()
    # DocuSeal returns a list of submitters (one submission). Normalize the id.
    submission_id = None
    if isinstance(data, list) and data:
        submission_id = data[0].get("submission_id") or data[0].get("id")
        returned_submitters = data
    elif isinstance(data, dict):
        submission_id = data.get("id") or data.get("submission_id")
        returned_submitters = data.get("submitters", [])
    else:
        returned_submitters = []
    return {"success": True, "submission_id": submission_id, "submitters": returned_submitters}


def get_submission(submission_id: int | str) -> dict:
    """Fetch the authoritative submission status from DocuSeal.

    Returns ``{"success": True, "status": ..., "submission": {...}}``. ``status``
    is one of DocuSeal's values (pending / completed / declined / expired).
    """
    if not is_configured():
        return _not_configured()
    try:
        resp = requests.get(
            f"{DOCUSEAL_BASE_URL}/submissions/{submission_id}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("DocuSeal get submission failed: %s", e)
        return {"success": False, "error": "Could not reach the e-signature service."}

    if resp.status_code == 404:
        return {"success": False, "error": "Submission not found.", "status_code": 404}
    if resp.status_code >= 400:
        logger.error("DocuSeal get submission %s: %s", resp.status_code, resp.text[:300])
        return {"success": False, "error": f"E-signature service error ({resp.status_code})."}

    submission = resp.json()
    return {
        "success": True,
        "status": submission.get("status"),
        "submission": submission,
    }


def get_signed_document_urls(submission: dict) -> list[str]:
    """Extract URLs of the completed/signed documents from a submission object.

    DocuSeal exposes completed files under each submitter's ``documents`` (and a
    submission-level ``documents``). Returns a de-duplicated list of URLs.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def _collect(docs):
        for doc in docs or []:
            url = doc.get("url") if isinstance(doc, dict) else None
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

    if isinstance(submission, dict):
        _collect(submission.get("documents"))
        for sub in submission.get("submitters", []) or []:
            if isinstance(sub, dict):
                _collect(sub.get("documents"))
    return urls


def verify_webhook_secret(provided: str | None) -> bool:
    """Constant-time check of the webhook shared secret.

    If no secret is configured we cannot verify the shared secret, so this
    returns False — callers must then rely on re-fetching the submission via the
    authenticated API (the authoritative check). When a secret IS configured,
    the provided header value must match it exactly.
    """
    if not DOCUSEAL_WEBHOOK_SECRET:
        return False
    if not provided:
        return False
    return hmac.compare_digest(str(provided), DOCUSEAL_WEBHOOK_SECRET)
