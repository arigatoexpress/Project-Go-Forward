"""Partner webhook dispatch for /api/v1/* integrations.

Design
------
- Partners register webhooks via env vars: `PARTNER_WEBHOOK_URL_<PARTNER>`
  (e.g., `PARTNER_WEBHOOK_URL_ETAI=https://notion.example.com/hooks/deals`).
  Mirrors the multi-key pattern (`THO_API_KEY_*`) so the same partner_id
  naming applies across auth and webhooks.
- Every webhook body is signed with HMAC-SHA256 using the shared secret
  `PARTNER_WEBHOOK_SIGNING_KEY` (stored in Secret Manager, mounted as env).
  Every delivery carries two signatures:
  - `X-THO-Signature-V2: v2=<hex>` with `X-THO-Timestamp: <unix seconds>`:
    HMAC over the timestamp, event, partner, delivery id and raw body (see
    `signed_message_v2`). Receivers should verify this one with
    `verify_partner_webhook`, which also enforces a replay window.
  - `X-THO-Signature: sha256=<hex>`: legacy HMAC(key, raw_body), kept so
    existing receivers keep working while they migrate.
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
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import requests

from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

logger = logging.getLogger(__name__)

_DELIVERY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="partner-webhook")

_DEFAULT_TIMEOUT_SECONDS = 8.0
_unsigned_dispatch_blocked_logged = False


def _reset_unsigned_guard_for_tests() -> None:
    """Test hook: allow the once-per-process unsigned-dispatch error again."""
    global _unsigned_dispatch_blocked_logged
    _unsigned_dispatch_blocked_logged = False


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


SIGNATURE_HEADER = "X-THO-Signature"
SIGNATURE_V2_HEADER = "X-THO-Signature-V2"
TIMESTAMP_HEADER = "X-THO-Timestamp"
DEFAULT_TOLERANCE_SECONDS = 300


def signed_message_v2(
    timestamp: str, event: str, partner_id: str, delivery_id: str, body: bytes
) -> bytes:
    """Bytes covered by the v2 signature. Newline-separated header values, then the body.

    Header values cannot contain newlines, so the encoding is unambiguous.
    """
    for value in (timestamp, event, partner_id, delivery_id):
        if "\n" in value or "\r" in value:
            raise ValueError("webhook header values must not contain newlines")
    head = "\n".join(("v2", timestamp, event, partner_id, delivery_id)) + "\n"
    return head.encode("utf-8") + body


def _sign_v2(
    timestamp: str, event: str, partner_id: str, delivery_id: str, body: bytes, signing_key: str
) -> str:
    message = signed_message_v2(timestamp, event, partner_id, delivery_id, body)
    return "v2=" + hmac.new(signing_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signed_headers(
    event: str,
    partner_id: str,
    delivery_id: str,
    body: bytes,
    signing_key: str,
    *,
    now: float | None = None,
) -> dict[str, str]:
    """Headers for one delivery: legacy body signature plus timestamped v2 signature."""
    timestamp = str(int(time.time() if now is None else now))
    return {
        "Content-Type": "application/json",
        "X-THO-Event": event,
        "X-THO-Partner": partner_id,
        "X-THO-Delivery": delivery_id,
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: _sign(body, signing_key),
        SIGNATURE_V2_HEADER: _sign_v2(timestamp, event, partner_id, delivery_id, body, signing_key),
    }


def verify_partner_webhook(
    headers: Mapping[str, str],
    body: bytes,
    signing_key: str,
    *,
    now: float | None = None,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    replay_guard=None,
    allow_legacy: bool = False,
) -> bool:
    """Receiver-side check for a THO partner webhook. Fails closed.

    Verifies the v2 signature, rejects timestamps outside ``tolerance_seconds``
    and, when a ``tools.webhook_replay.ReplayGuard`` is passed, rejects a
    delivery id seen before. ``allow_legacy=True`` accepts a body-only
    signature when v2 headers are absent; that path has no replay protection
    and exists only for migration.
    """
    if not signing_key:
        return False
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    v2 = lower.get(SIGNATURE_V2_HEADER.lower(), "")
    timestamp = lower.get(TIMESTAMP_HEADER.lower(), "")
    if not v2 or not timestamp:
        if not allow_legacy:
            return False
        legacy = lower.get(SIGNATURE_HEADER.lower(), "")
        return bool(legacy) and hmac.compare_digest(_sign(body, signing_key), legacy)
    if not timestamp.isdigit():
        return False
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > tolerance_seconds:
        return False
    event = lower.get("x-tho-event", "")
    partner_id = lower.get("x-tho-partner", "")
    delivery_id = lower.get("x-tho-delivery", "")
    try:
        expected = _sign_v2(timestamp, event, partner_id, delivery_id, body, signing_key)
    except ValueError:
        return False
    if not hmac.compare_digest(expected, v2):
        return False
    if replay_guard is not None and not replay_guard.claim(
        f"tho-partner:{partner_id}:{delivery_id}"
    ):
        return False
    return True


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
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        db.collection("activities").document(delivery_id).set(
            activity, timeout=FIRESTORE_RPC_TIMEOUT
        )
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
        "delivered_at": datetime.now(UTC).isoformat(),
        "idempotency_key": delivery_id,
        "data": payload,
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")

    if signing_key:
        headers = signed_headers(event, partner_id, delivery_id, body_bytes, signing_key)
    else:
        headers = {
            "Content-Type": "application/json",
            "X-THO-Event": event,
            "X-THO-Partner": partner_id,
            "X-THO-Delivery": delivery_id,
        }

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
            partner_id,
            event,
            status_code,
            delivery_id,
        )
    else:
        logger.warning(
            "Partner webhook delivery failed partner_id=%s event=%s status=%s error=%s delivery_id=%s",
            partner_id,
            event,
            status_code,
            error,
            delivery_id,
        )

    _log_delivery(db, partner_id, event, delivery_id, status_code, success, error, url)


def dispatch_partner_event(
    event: str,
    payload: dict[str, Any],
    db=None,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    blocking: bool = False,
    partner_ids: list[str] | None = None,
) -> list[str]:
    """Dispatch `event` to configured partner webhooks.

    Returns the list of partner_ids that received a delivery attempt.
    If partner_ids is provided, only those partners are targeted.
    """
    global _unsigned_dispatch_blocked_logged
    urls = _get_partner_webhook_urls()
    signing_key = _get_signing_key()

    if not urls:
        return []

    if not signing_key:
        if not _unsigned_dispatch_blocked_logged:
            logger.error(
                "Partner webhook URLs configured but PARTNER_WEBHOOK_SIGNING_KEY "
                "is unset; refusing unsigned dispatch"
            )
            _unsigned_dispatch_blocked_logged = True
        return []

    targets = list(urls.items())
    if partner_ids is not None:
        allowed = {p.lower() for p in partner_ids}
        targets = [(pid, url) for pid, url in targets if pid in allowed]

    if not targets:
        return []

    futures = []
    for partner_id, url in targets:
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

    return [partner_id for partner_id, _ in targets]
