#!/usr/bin/env python3
"""Read-only readiness audit for THO's native Google growth stack.

The command checks API enablement, credential *presence*, and runtime tag
configuration. It never reads a secret payload, prints an identifier value, or
mutates Google Cloud / Google Ads state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable

SERVICES = (
    "googleads.googleapis.com",
    "searchconsole.googleapis.com",
    "analyticsadmin.googleapis.com",
    "analyticsdata.googleapis.com",
    "mybusinessbusinessinformation.googleapis.com",
    "businessprofileperformance.googleapis.com",
)

SECRETS = (
    "google-ads-developer-token",
    "google-ads-client-id",
    "google-ads-client-secret",
    "google-ads-refresh-token",
)

RUNTIME_NAMES = (
    "GA4_MEASUREMENT_ID",
    "GTM_CONTAINER_ID",
)


def _call(command: list[str], runner: Callable) -> tuple[bool, str]:
    completed = runner(command, capture_output=True, text=True, check=False)
    return completed.returncode == 0, completed.stdout if completed.returncode == 0 else ""


def _runtime_env_names(payload) -> set[str]:
    """Accept both gcloud's wrapped JSON and older direct-list output."""
    if isinstance(payload, dict):
        payload = (
            payload.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
            .get("env", [])
        )
    if not isinstance(payload, list):
        raise ValueError("unexpected Cloud Run env shape")
    return {item.get("name") for item in payload if isinstance(item, dict) and item.get("name")}


def audit(project: str, service: str, region: str, *, runner=subprocess.run) -> dict:
    """Return presence-only readiness data. Command stderr is never retained."""
    project_flag = f"--project={project}"
    errors = []

    ok, output = _call(
        ["gcloud", "services", "list", "--enabled", project_flag, "--format=value(config.name)"],
        runner,
    )
    enabled = set(output.splitlines()) if ok else set()
    if not ok:
        errors.append("services")

    ok, output = _call(
        ["gcloud", "secrets", "list", project_flag, "--format=value(name)"],
        runner,
    )
    secret_names = set(output.splitlines()) if ok else set()
    if not ok:
        errors.append("secrets")

    ok, output = _call(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            service,
            f"--region={region}",
            project_flag,
            "--format=json(spec.template.spec.containers[0].env)",
        ],
        runner,
    )
    runtime_names = set()
    if ok:
        try:
            runtime_names = _runtime_env_names(json.loads(output or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError, IndexError):
            errors.append("runtime")
    else:
        errors.append("runtime")

    return {
        "project": project,
        "service": service,
        "region": region,
        "services": {name: name in enabled for name in SERVICES},
        "secrets": {name: name in secret_names for name in SECRETS},
        "runtime": {name: name in runtime_names for name in RUNTIME_NAMES},
        "spend_enabled": False,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="tho-ai-agent")
    parser.add_argument("--service", default="project-go-forward")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 unless all prerequisites are present"
    )
    args = parser.parse_args()

    result = audit(args.project, args.service, args.region)
    print(json.dumps(result, indent=2, sort_keys=True))
    ready = (
        not result["errors"]
        and all(result["services"].values())
        and all(result["secrets"].values())
        and any(result["runtime"].values())
    )
    return 1 if args.strict and not ready else 0


if __name__ == "__main__":
    raise SystemExit(main())
