"""
gmail_sync.py
=============

Read-only Gmail inbox scaffold for ingesting customer replies into the THO CRM.

This module is the FIRST HALF of closing the customer-reply loop. The OUTBOUND
path already works (see ``email_service.py`` -> Resend). Inbound replies sent to
``texashomeoutlet.com`` addresses are currently invisible to the CRM.

This scaffold ships GATED OFF — production behavior is unchanged until Mark /
Celeste complete the Workspace setup checklist in
``docs/integration/gmail-inbound-setup.md`` and the env flag
``GMAIL_INBOUND_ENABLED`` is flipped to ``true``.

Auth model
----------

* Workspace **service account** + **domain-wide delegation** to a delegated
  Workspace user (typically ``ops@texashomeoutlet.com``).
* The service account's JSON key lives in Secret Manager
  (``gmail-service-account-json``) and is mounted into Cloud Run at the path
  pointed to by ``GMAIL_SERVICE_ACCOUNT_JSON``.
* Scopes: ``https://www.googleapis.com/auth/gmail.modify`` (read messages,
  apply labels). We do NOT use the broader ``gmail.full`` scope; we never
  need to send mail through this path (Resend handles outbound).

Why service account + DWD instead of OAuth or IMAP
--------------------------------------------------

* OAuth would require a human refresh-token dance and per-user consent.
* IMAP works but exposes credentials (app password) and is harder to scope.
* DWD is the standard Workspace pattern for backend integrations and lets the
  Workspace admin revoke access centrally without touching code.

PII Posture
-----------

* This module only **fetches and parses** messages — it does NOT route them
  into Firestore, the CRM, or any LLM. A follow-up PR wires that.
* No message body is logged. Only message IDs and counts are emitted.
* The ``poll`` and ``mark_processed`` methods are intentionally minimal so the
  caller (when it exists) controls retention and deletion semantics.

Usage (after activation)
------------------------

    from tools.gmail_sync import GmailInboxSync

    sync = GmailInboxSync(
        service_account_json_path="/secrets/gmail/sa.json",
        delegated_user="ops@texashomeoutlet.com",
        label="inbox",
    )
    for msg in sync.poll(since_iso="2026-04-30T00:00:00Z", max_messages=50):
        # route msg into CRM, then:
        sync.mark_processed(msg.gmail_message_id)
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

LOG = logging.getLogger("gmail_sync")

#: OAuth scope required for read + label-apply. Narrowest scope that supports
#: ``users.messages.modify`` (label application).
GMAIL_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.modify",)

#: Label applied to processed messages so subsequent polls can skip them.
PROCESSED_LABEL_NAME: str = "tho-processed"


def _missing_google_libs(exc: ImportError) -> SystemExit:
    raise SystemExit(
        "Missing Google client libs. Run: pip install " "google-api-python-client google-auth"
    ) from exc


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """A single Gmail message, parsed into the fields the CRM cares about."""

    gmail_message_id: str
    from_addr: str
    to_addr: str
    subject: str
    body_text: str
    body_html: str
    timestamp: datetime
    raw_headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Header / payload parsing helpers (pure, easy to test)
# ---------------------------------------------------------------------------


def _headers_to_dict(payload: dict[str, Any]) -> dict[str, str]:
    """Flatten Gmail's list-of-headers into a case-insensitive dict (last wins)."""
    out: dict[str, str] = {}
    for h in payload.get("headers", []) or []:
        name = (h.get("name") or "").strip()
        value = h.get("value") or ""
        if name:
            out[name.lower()] = value
    return out


def _decode_b64url(data: str) -> str:
    """Decode Gmail's base64url body. Returns "" on any decode failure."""
    if not data:
        return ""
    try:
        # Gmail uses URL-safe base64 without padding
        padded = data + "=" * ((4 - len(data) % 4) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - defensive
        return ""


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    """Walk a message payload and return ``(text, html)`` body parts.

    Gmail messages can be flat (single part) or multipart (text/html alternative,
    with attachments, etc.). We do a depth-first walk and pick the FIRST
    text/plain and FIRST text/html bodies we find. Anything else is ignored.
    """
    text = ""
    html = ""

    def visit(p: dict[str, Any]) -> None:
        nonlocal text, html
        mime = p.get("mimeType", "")
        body = p.get("body") or {}
        data = body.get("data", "")
        if mime == "text/plain" and not text and data:
            text = _decode_b64url(data)
        elif mime == "text/html" and not html and data:
            html = _decode_b64url(data)
        for child in p.get("parts", []) or []:
            visit(child)

    visit(payload)
    return text, html


def _parse_timestamp(headers: dict[str, str], internal_date_ms: str | None) -> datetime:
    """Prefer the RFC 2822 ``Date`` header; fall back to Gmail's ``internalDate``."""
    date_hdr = headers.get("date", "")
    if date_hdr:
        try:
            parsed = parsedate_to_datetime(date_hdr)
            if parsed is not None:
                # Normalize to aware UTC
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            pass
    if internal_date_ms:
        try:
            return datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=UTC)
        except (TypeError, ValueError):
            pass
    return datetime.now(tz=UTC)


def parse_message(raw: dict[str, Any]) -> Message:
    """Convert a Gmail ``users.messages.get`` response into a :class:`Message`.

    Pure function — easy to unit-test without any network IO.
    """
    payload = raw.get("payload") or {}
    headers = _headers_to_dict(payload)
    text, html = _extract_bodies(payload)
    return Message(
        gmail_message_id=raw.get("id", ""),
        from_addr=headers.get("from", ""),
        to_addr=headers.get("to", ""),
        subject=headers.get("subject", ""),
        body_text=text,
        body_html=html,
        timestamp=_parse_timestamp(headers, raw.get("internalDate")),
        raw_headers=headers,
    )


# ---------------------------------------------------------------------------
# Gmail client wrapper
# ---------------------------------------------------------------------------


class GmailInboxSync:
    """Thin wrapper around the Gmail API for inbox polling + label management.

    Construction is cheap — no network calls happen until :meth:`poll` or
    :meth:`mark_processed` is invoked. This keeps the scaffold inert when the
    feature flag is off and makes unit testing trivial via mocked
    ``googleapiclient.discovery.build``.
    """

    def __init__(
        self,
        service_account_json_path: str,
        delegated_user: str,
        label: str = "inbox",
    ) -> None:
        if not service_account_json_path:
            raise ValueError("service_account_json_path is required")
        if not delegated_user:
            raise ValueError("delegated_user is required")
        self.service_account_json_path = service_account_json_path
        self.delegated_user = delegated_user
        self.label = label
        self._service: Any | None = None
        self._processed_label_id: str | None = None

    # -- Auth / service construction ----------------------------------------

    def _build_service(self) -> Any:
        """Lazily build (and cache) the Gmail API client.

        Uses domain-wide delegation: the service account impersonates
        ``delegated_user`` so API calls run with that user's mailbox scope.
        """
        if self._service is not None:
            return self._service
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - runtime environment only
            raise _missing_google_libs(exc) from exc

        creds = service_account.Credentials.from_service_account_file(
            self.service_account_json_path,
            scopes=list(GMAIL_SCOPES),
        )
        delegated = creds.with_subject(self.delegated_user)
        self._service = build("gmail", "v1", credentials=delegated, cache_discovery=False)
        return self._service

    # -- Query construction --------------------------------------------------

    def _build_query(self, since_iso: str | None) -> str:
        """Construct a Gmail search query.

        * Always restrict to the configured label (default ``inbox``).
        * Always exclude messages already tagged ``tho-processed``.
        * If ``since_iso`` is provided, add a ``after:`` clause (Gmail's date
          filter takes ``YYYY/MM/DD``; we pass the date portion).
        """
        parts = [f"label:{self.label}", f"-label:{PROCESSED_LABEL_NAME}"]
        if since_iso:
            try:
                dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                parts.append(f"after:{dt.strftime('%Y/%m/%d')}")
            except ValueError:
                LOG.warning("Ignoring unparseable since_iso=%r", since_iso)
        return " ".join(parts)

    # -- Public API ---------------------------------------------------------

    def poll(self, since_iso: str | None = None, max_messages: int = 50) -> list[Message]:
        """Fetch up to ``max_messages`` recent messages matching the inbound filter.

        Returns parsed :class:`Message` objects. Does NOT apply any labels —
        the caller decides what counts as "processed" and explicitly calls
        :meth:`mark_processed` once a message has been routed into the CRM.

        Network behavior
        ----------------
        Two-step: ``users.messages.list`` (returns IDs) then
        ``users.messages.get`` per ID (returns full payload). Gmail does not
        support a single batched-fetch RPC for full bodies. For a backlog of
        50 messages this is ~51 round-trips; acceptable for a polling cadence
        of minutes, not seconds.
        """
        if max_messages <= 0:
            return []
        svc = self._build_service()
        query = self._build_query(since_iso)
        LOG.info("Polling Gmail: query=%r max=%d", query, max_messages)

        list_resp = (
            svc.users().messages().list(userId="me", q=query, maxResults=max_messages).execute()
        )
        ids = [m["id"] for m in (list_resp.get("messages") or []) if m.get("id")]

        out: list[Message] = []
        for mid in ids:
            raw = svc.users().messages().get(userId="me", id=mid, format="full").execute()
            out.append(parse_message(raw))
        return out

    def mark_processed(self, gmail_message_id: str) -> None:
        """Apply the ``tho-processed`` label to a message.

        Creates the label on first use and caches its ID for subsequent calls.
        Idempotent: applying a label that's already on a message is a no-op.
        """
        if not gmail_message_id:
            raise ValueError("gmail_message_id is required")
        svc = self._build_service()
        label_id = self._ensure_processed_label(svc)
        svc.users().messages().modify(
            userId="me",
            id=gmail_message_id,
            body={"addLabelIds": [label_id], "removeLabelIds": []},
        ).execute()
        LOG.info("Marked %s as %s", gmail_message_id, PROCESSED_LABEL_NAME)

    # -- Label management ---------------------------------------------------

    def _ensure_processed_label(self, svc: Any) -> str:
        """Find or create the ``tho-processed`` label and return its ID."""
        if self._processed_label_id:
            return self._processed_label_id
        labels_resp = svc.users().labels().list(userId="me").execute()
        for lab in labels_resp.get("labels", []) or []:
            if lab.get("name") == PROCESSED_LABEL_NAME:
                self._processed_label_id = lab["id"]
                return self._processed_label_id  # type: ignore[return-value]
        # Not found — create it.
        created = (
            svc.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": PROCESSED_LABEL_NAME,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._processed_label_id = created["id"]
        return self._processed_label_id  # type: ignore[return-value]
