"""Partner webhook dispatch for /api/v1/* integrations.

Design
------
- Partners register webhooks via env vars: `PARTNER_WEBHOOK_URL_<PARTNER>`
  (e.g., `PARTNER_WEBHOOK_URL_ETAI=https://notion.example.com/hooks/deals`).
  Mirrors the multi-key pattern (`THO_API_KEY_*`) so the same partner_id
  naming applies across auth and webhooks.
- Every webhook body is signed with HMAC-SHA256 using the shared secret
  `PARTNER_WEBHOOK_SIGNING_KEY` (stored in Secret Manager, mounted as env).
  Partners verify by recomputing HMAC(key, raw_body) and comparing to
  the `X-THO-Signature: sha256=<hex>` header.
- Delivery is fire-and-forget on a small thread pool (max_workers=4) so
  the admin PUT endpoint isn't blocked on remote latency.
- Each attempt writes a row to the Firestore `activities/` collection so
  delivery outcomes are auditable and replayable.

Why no retry logic in this phase
--------------------------------
Partners receiving these webhooks should be idempotent (we send an
idempotency_key in the body) and can poll `/api/v1/stats` for reconciliation.
Retry/backoff adds complexity and a queue dependency we don't need yet.
If delivery reliability becomes a real problem, graduate to Cloud Tasks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DELIVERY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="partner-webhook")

_DEFAULT_TIMEOUT_SECONDS = 8.0


def _get_partner_webhook_urls() -> dict[str, str]:
    """Return a mapping of {partner_id: url} from env vars.

    `PARTNER_WEBHOOK_URL_<ID>` → partner_id is lowercased suffix.
    Example: PARTNER_WEBHOOK_URL_ETAI=https://... → {"etai": "https://..."}.
    """
    urls: dict[str, str] = {}
    prefix = "PARTNER_WEBHOOK_URL_"
    for name, value in os.environ.items():
        if not name.startswith(prefix):
            continue
        partner_id = name[len(prefix) :].lower()
        stripped = (value or "").strip()
        if stripped and partner_id:
            urls[partner_id] = stripped
    return urls


def _get_signing_key() -> str:
    return (os.environ.get("PARTNER_WEBHOOK_SIGNING_KEY") or "").strip()


def _sign(body_bytes: bytes, signing_key: str) -> str:
    digest = hmac.new(signing_key.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _log_delivery(
    db,
    partner_id: str,
    event: str,
    delivery_id: str,
    status_code: int | None,
    success: bool,
    error: str | None,
    url: str,
) -> None:
    """Persist delivery outcome to Firestore `activities/` for audit."""
    if db is None:
        return
    activity = {
        "id": delivery_id,
        "activity_type": f"partner_webhook_delivery.{event}",
        "description": f"Outbound webhook → {partner_id}: {event}",
        "actor": "partner_webhook_dispatcher",
        "metadata": {
            "partner_id": partner_id,
            "event": event,
            "url": url,
            "status_code": status_code,
            "success": success,
            "error": error,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.collection("activities").document(delivery_id).set(activity)
    except Exception as e:
        logger.warning("activities/ log failed for webhook delivery: %s", e)


def _deliver_one(
    partner_id: str,
    url: str,
    event: str,
    payload: dict,
    signing_key: str,
    db,
    timeout_seconds: float,
) -> None:
    """Blocking single delivery. Runs on the thread pool."""
    delivery_id = str(uuid.uuid4())
    body = {
        "event": event,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": delivery_id,
        "data": payload,
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-THO-Event": event,
        "X-THO-Partner": partner_id,
        "X-THO-Delivery": delivery_id,
    }
    if signing_key:
        headers["X-THO-Signature"] = _sign(body_bytes, signing_key)

    status_code: int | None = None
    success = False
    error: str | None = None
    try:
        resp = requests.post(url, data=body_bytes, headers=headers, timeout=timeout_seconds)
        status_code = resp.status_code
        success = 200 <= resp.status_code < 300
        if not success:
            error = f"HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.exceptions.RequestException as e:
        error = f"{type(e).__name__}: {str(e)[:200]}"
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:200]}"

    if success:
        logger.info(
            "Partner webhook delivered partner_id=%s event=%s status=%s delivery_id=%s",
            partner_id, event, status_code, delivery_id,
        )
    else:
        logger.warning(
            "Partner webhook delivery failed partner_id=%s event=%s status=%s error=%s delivery_id=%s",
            partner_id, event, status_code, error, delivery_id,
        )

    _log_delivery(db, partner_id, event, delivery_id, status_code, success, error, url)


def dispatch_partner_event(
    event: str,
    payload: dict[str, Any],
    db=None,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    blocking: bool = False,
) -> list[str]:
    """Dispatch `event` to every configured partner webhook.

    Returns the list of partner_ids that received a delivery attempt
    (useful for tests and for the admin endpoint to confirm dispatch).

    If no partner webhooks are configured, returns [] and does nothing —
    this is the expected state until a partner's URL is set.

    Parameters
    ----------
    event : str            e.g. "deal.status_changed", "deal.funded"
    payload : dict         event-specific body. Must be JSON-serializable.
    db : THODatabase | None
        When provided, each delivery attempt is logged to the
        `activities/` collection. Accepts the singleton or a fake.
    timeout_seconds : float
        Per-request timeout. Default 8s — long enough for a slow partner,
        short enough that a dead URL doesn't hold a worker thread forever.
    blocking : bool
        If True, wait for all deliveries to complete before returning.
        Default False (fire-and-forget). Used by tests for determinism.
    """
    urls = _get_partner_webhook_urls()
    signing_key = _get_signing_key()

    if not urls:
        return []

    partner_ids = list(urls.keys())
    futures = []
    for partner_id, url in urls.items():
        fut = _DELIVERY_POOL.submit(
            _deliver_one,
            partner_id,
            url,
            event,
            payload,
            signing_key,
            db,
            timeout_seconds,
        )
        futures.append(fut)

    if blocking:
        for fut in futures:
            try:
                fut.result(timeout=timeout_seconds + 2.0)
            except Exception as e:
                logger.warning("Partner webhook future error: %s", e)

    return partner_ids
