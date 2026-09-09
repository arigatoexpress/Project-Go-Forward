"""Fail-closed runtime gates for owner-approved PAUSED Google Ads creation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_REGION_RE = re.compile(r"^[a-z]+(?:-[a-z0-9]+)+$")
_JOB_RE = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")


def _exact_true(name: str) -> bool:
    return os.environ.get(name) == "true"


@dataclass(frozen=True)
class PausedCreateApprovalRuntime:
    """Presence-only current-revision gate; absent or malformed config is false."""

    feature_enabled: bool
    cloud_readiness_verified: bool
    iam_verified: bool
    revision_bound: bool
    dispatcher_configured: bool
    project: str | None
    region: str | None
    job: str | None

    @property
    def approval_available(self) -> bool:
        return (
            self.feature_enabled
            and self.cloud_readiness_verified
            and self.iam_verified
            and self.revision_bound
            and self.dispatcher_configured
        )

    @classmethod
    def from_env(cls) -> PausedCreateApprovalRuntime:
        app_revision = os.environ.get("APP_VERSION", "")
        readiness_revision = os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION", "")
        project = os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT", "")
        region = os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_REGION", "")
        job = os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_JOB", "")
        revision_bound = bool(
            _REVISION_RE.fullmatch(app_revision) and readiness_revision == app_revision
        )
        dispatcher_configured = bool(
            _PROJECT_RE.fullmatch(project)
            and _REGION_RE.fullmatch(region)
            and _JOB_RE.fullmatch(job)
        )
        return cls(
            feature_enabled=_exact_true("THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED"),
            cloud_readiness_verified=_exact_true(
                "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED"
            ),
            iam_verified=_exact_true("THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED"),
            revision_bound=revision_bound,
            dispatcher_configured=dispatcher_configured,
            project=project if dispatcher_configured else None,
            region=region if dispatcher_configured else None,
            job=job if dispatcher_configured else None,
        )

    def sanitized_gates(self) -> dict[str, bool]:
        return {
            "feature_enabled": self.feature_enabled,
            "cloud_readiness_verified": self.cloud_readiness_verified,
            "iam_verified": self.iam_verified,
            "revision_bound": self.revision_bound,
            "dispatcher_configured": self.dispatcher_configured,
        }
