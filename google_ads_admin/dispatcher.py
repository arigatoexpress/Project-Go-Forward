"""Fixed Cloud Run v2 dispatcher adapter; no Ads provider or runtime overrides."""

from __future__ import annotations

import re
from typing import Any

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_REGION_RE = re.compile(r"^[a-z]+(?:-[a-z0-9]+)+$")
_JOB_RE = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")


class DispatchError(RuntimeError):
    """Sanitized fixed-dispatch failure."""


class FixedCloudRunJobDispatcher:
    """Invoke exactly one configured job with an empty v2 ``run`` request body."""

    def __init__(
        self,
        *,
        project: str,
        region: str,
        job: str,
        transport: Any | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        if (
            not _PROJECT_RE.fullmatch(project)
            or not _REGION_RE.fullmatch(region)
            or not _JOB_RE.fullmatch(job)
        ):
            raise ValueError("fixed dispatcher configuration is invalid")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("dispatcher timeout must be between 1 and 30 seconds")
        self._url = (
            f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run"
        )
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _default_transport():
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return AuthorizedSession(credentials)

    def invoke(self) -> None:
        transport = self._transport or self._default_transport()
        try:
            response = transport.post(
                self._url,
                json={},
                timeout=self._timeout_seconds,
            )
        except Exception:
            raise DispatchError("job_invocation_failed") from None
        if not getattr(response, "ok", False):
            raise DispatchError("job_invocation_failed")
