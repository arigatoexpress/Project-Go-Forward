"""Pure, sanitized Google Ads account-access evidence contract.

The contract deliberately contains no credential, account, request, resource,
response, or free-form error field. It is shared by the fixed job entrypoint
and the Firestore adapter so digest and freshness validation cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

MAX_EVIDENCE_TTL = timedelta(minutes=15)
DEFAULT_EVIDENCE_TTL = timedelta(minutes=5)
_DEPLOYMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$")
_SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_EVIDENCE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class AccessCheckKey(StrEnum):
    GOOGLE_ADS_ACCOUNT_ACCESS_GREEN = "google_ads_account_access_green"


class AccessEvidenceStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class InvalidAccessEvidence(ValueError):
    """Sanitized failure raised for evidence that is unsafe or stale."""


@dataclass(frozen=True)
class AccessEvidence:
    deployment_id: str
    check_key: AccessCheckKey
    status: AccessEvidenceStatus
    observed_at: datetime
    expires_at: datetime
    source_revision: str
    evidence_digest: str


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidAccessEvidence("evidence_timestamp_invalid")
    return value.astimezone(UTC)


def validate_source_revision(value: str) -> str:
    if not isinstance(value, str) or not _SOURCE_REVISION_RE.fullmatch(value):
        raise InvalidAccessEvidence("source_revision_invalid")
    return value


def _digest_timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def compute_evidence_digest(
    *,
    deployment_id: str,
    check_key: AccessCheckKey,
    status: AccessEvidenceStatus,
    observed_at: datetime,
    expires_at: datetime,
    source_revision: str,
) -> str:
    canonical = json.dumps(
        {
            "check_key": check_key.value,
            "deployment_id": deployment_id,
            "expires_at": _digest_timestamp(expires_at),
            "observed_at": _digest_timestamp(observed_at),
            "source_revision": source_revision,
            "status": status.value,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate_access_evidence(evidence: AccessEvidence, *, now: datetime) -> AccessEvidence:
    now = _utc(now)
    if not isinstance(evidence, AccessEvidence):
        raise InvalidAccessEvidence("access_evidence_invalid")
    if not _DEPLOYMENT_ID_RE.fullmatch(evidence.deployment_id):
        raise InvalidAccessEvidence("deployment_id_invalid")
    if not isinstance(evidence.check_key, AccessCheckKey):
        raise InvalidAccessEvidence("check_key_invalid")
    if not isinstance(evidence.status, AccessEvidenceStatus):
        raise InvalidAccessEvidence("status_invalid")
    observed_at = _utc(evidence.observed_at)
    expires_at = _utc(evidence.expires_at)
    source_revision = validate_source_revision(evidence.source_revision)
    if observed_at > now:
        raise InvalidAccessEvidence("evidence_observed_in_future")
    if expires_at <= now or expires_at <= observed_at:
        raise InvalidAccessEvidence("evidence_expired")
    if expires_at - observed_at > MAX_EVIDENCE_TTL:
        raise InvalidAccessEvidence("evidence_ttl_invalid")
    if not _EVIDENCE_DIGEST_RE.fullmatch(evidence.evidence_digest):
        raise InvalidAccessEvidence("evidence_digest_invalid")
    expected_digest = compute_evidence_digest(
        deployment_id=evidence.deployment_id,
        check_key=evidence.check_key,
        status=evidence.status,
        observed_at=observed_at,
        expires_at=expires_at,
        source_revision=source_revision,
    )
    if evidence.evidence_digest != expected_digest:
        raise InvalidAccessEvidence("evidence_digest_mismatch")
    return replace(
        evidence,
        observed_at=observed_at,
        expires_at=expires_at,
        source_revision=source_revision,
    )


def build_access_evidence(
    *,
    deployment_id: str,
    check_key: AccessCheckKey,
    status: AccessEvidenceStatus,
    observed_at: datetime,
    expires_at: datetime,
    source_revision: str,
    now: datetime,
) -> AccessEvidence:
    if not isinstance(check_key, AccessCheckKey):
        raise InvalidAccessEvidence("check_key_invalid")
    if not isinstance(status, AccessEvidenceStatus):
        raise InvalidAccessEvidence("status_invalid")
    observed_at = _utc(observed_at)
    expires_at = _utc(expires_at)
    source_revision = validate_source_revision(source_revision)
    candidate = AccessEvidence(
        deployment_id=deployment_id,
        check_key=check_key,
        status=status,
        observed_at=observed_at,
        expires_at=expires_at,
        source_revision=source_revision,
        evidence_digest=compute_evidence_digest(
            deployment_id=deployment_id,
            check_key=check_key,
            status=status,
            observed_at=observed_at,
            expires_at=expires_at,
            source_revision=source_revision,
        ),
    )
    return validate_access_evidence(candidate, now=now)


def evidence_payload(evidence: AccessEvidence) -> dict:
    """Return the complete and only persistable evidence field set."""
    return {
        "deployment_id": evidence.deployment_id,
        "check_key": evidence.check_key.value,
        "status": evidence.status.value,
        "observed_at": evidence.observed_at,
        "expires_at": evidence.expires_at,
        "source_revision": evidence.source_revision,
        "evidence_digest": evidence.evidence_digest,
    }
