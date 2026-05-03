"""
SMS Team Alert Service — Twilio integration, env-gated.

Activation: set all four env vars to enable.
  TWILIO_ACCOUNT_SID   — Twilio Account SID from console.twilio.com
  TWILIO_AUTH_TOKEN    — Twilio Auth Token from console.twilio.com
  TWILIO_FROM_NUMBER   — Purchased Twilio number, E.164 format (+1XXXXXXXXXX)
  TEAM_ALERT_PHONE_NUMBERS — Comma-separated recipient numbers, E.164 format

If any required env var is absent the service is a silent no-op; it never
raises, never crashes a request handler, and never blocks a response.
"""

import hashlib
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEDUP_WINDOW_SECONDS = 60

# Module-level digest → timestamp map; shared across all SMSService instances
# within the same process. Keys age out naturally as new entries are added.
_sent_digests: dict[str, float] = {}


def _message_digest(message: str) -> str:
    return hashlib.md5(message.encode()).hexdigest()  # noqa: S324 — non-security use


class SMSService:
    """Best-effort SMS team alert dispatcher backed by Twilio.

    All public methods swallow exceptions so callers never need try/except.
    """

    def __init__(self) -> None:
        self._sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self._token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self._from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
        raw = os.environ.get("TEAM_ALERT_PHONE_NUMBERS", "")
        self._to_numbers = [n.strip() for n in raw.split(",") if n.strip()]

        self._enabled = bool(
            self._sid and self._token and self._from_number and self._to_numbers
        )
        if not self._enabled:
            logger.info(
                "SMS alerts disabled — TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                "TWILIO_FROM_NUMBER, and TEAM_ALERT_PHONE_NUMBERS must all be set"
            )

    # ── Public API ──────────────────────────────────────────────────────────

    def send_team_alert(self, message: str, urgency: str = "normal") -> None:
        """Send `message` to all configured team numbers.

        No-ops silently when:
          - required env vars are absent
          - an identical message was sent within the last 60 seconds
          - the Twilio API call fails (error is logged, not raised)
        """
        if not self._enabled:
            return

        digest = _message_digest(message)
        now = time.time()
        if digest in _sent_digests and now - _sent_digests[digest] < _DEDUP_WINDOW_SECONDS:
            logger.info("SMS alert suppressed (duplicate within 60s)", extra={"digest": digest})
            return

        _sent_digests[digest] = now
        try:
            self._dispatch(message, urgency)
        except Exception as exc:  # noqa: BLE001
            logger.error("SMS team alert failed (non-fatal): %s", exc)

    # ── Internal ────────────────────────────────────────────────────────────

    def _dispatch(self, message: str, urgency: str) -> None:
        try:
            from twilio.rest import Client  # lazy import — optional dependency

            client = Client(self._sid, self._token)
            for number in self._to_numbers:
                client.messages.create(body=message, from_=self._from_number, to=number)
            logger.info(
                "SMS team alert sent",
                extra={"recipients": len(self._to_numbers), "urgency": urgency},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("SMS send failed (non-fatal): %s", exc)
