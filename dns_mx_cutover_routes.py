"""FastAPI router for the DNS/MX cutover workstream.

All routes live under ``/api/v1/cutover/*`` and use the partner API key auth
provided by ``main.py``.  Outbound notifications are dispatched via
``tools/partner_webhooks.py`` so receiving partners can verify the HMAC-SHA256
signature on ``X-THO-Signature``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

import dns_mx_cutover
from tools.partner_webhooks import dispatch_partner_event

router = APIRouter(prefix="/api/v1/cutover", tags=["cutover"])
logger = logging.getLogger(__name__)


def _get_db(request: Request):
    """Best-effort access to the THO database singleton attached to main.app."""
    return getattr(request.app.state, "db", None)


@router.get("/dns-status")
async def cutover_dns_status(request: Request):
    """Return the current apex-A and www-CNAME cutover status.

    No secrets are exposed; only public DNS resolution results and the
    configured expected values are returned.
    """
    try:
        return dns_mx_cutover.get_dns_cutover_status()
    except Exception as e:
        logger.error("DNS cutover status failed: %s", e)
        raise HTTPException(status_code=500, detail="DNS status check failed") from e


@router.get("/mx-status")
async def cutover_mx_status(request: Request):
    """Return inbound (Yahoo) and outbound (Resend) MX cutover status."""
    try:
        return dns_mx_cutover.get_mx_cutover_status()
    except Exception as e:
        logger.error("MX cutover status failed: %s", e)
        raise HTTPException(status_code=500, detail="MX status check failed") from e


@router.post("/notify")
async def cutover_notify(request: Request):
    """Dispatch an HMAC-signed partner webhook for a cutover event.

    Body (JSON):
      - ``event`` (str, required): e.g. ``dns.apex_ready``, ``mx.inbound_ready``,
        ``cutover.complete``.
      - ``payload`` (dict, optional): extra context merged into the webhook body.

    Returns the list of partner_ids that received a delivery attempt and the
    current combined cutover status.
    """
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    event = (data.get("event") or "").strip()
    if not event:
        raise HTTPException(status_code=400, detail="event is required")

    # Only allow cutover-scoped event names to prevent this endpoint from being
    # abused as a generic outbound messaging proxy.
    allowed_prefixes = ("dns.", "mx.", "cutover.")
    if not any(event.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(
            status_code=400,
            detail=f"event must start with one of {allowed_prefixes}",
        )

    payload: dict[str, Any] = data.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    status = dns_mx_cutover.get_full_cutover_status()
    merged_payload = {"cutover_status": status, **payload}

    try:
        partner_ids = dispatch_partner_event(
            event=f"cutover.{event}",
            payload=merged_payload,
            db=_get_db(request),
            blocking=True,
        )
    except Exception as e:
        logger.error("Cutover webhook dispatch failed: %s", e)
        raise HTTPException(status_code=500, detail="Webhook dispatch failed") from e

    return {
        "event": event,
        "dispatched_to": partner_ids,
        "cutover_status": status,
    }
