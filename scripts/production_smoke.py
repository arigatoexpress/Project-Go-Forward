#!/usr/bin/env python3
"""Production smoke checks for the public THO app.

The script is intentionally read-only by default. It checks public health,
public SPA routes, inventory payload shape, and admin-route protection
without submitting leads, appointments, documents, or marketing jobs.

Smoke-deal id convention
------------------------
The opt-in ``--check-empty-doc-rejection`` flag exercises a single live
write against the document API: a deliberately empty payload that *must*
be rejected with a 400. It does so under a reserved deal id namespace so
prod operators can recognise the records as smoke artefacts and never
mistake them for real customers.

Reserved smoke prefixes (do NOT use for real CRM data):

    smoke-empty-test     — empty document-generation regression probe
    smoke-*              — any other operator-marked smoke record

Any deal/customer/document whose id begins with ``smoke-`` is owned by
this script (or a related smoke harness) and is safe to delete.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://sapphirealpha.xyz"
PUBLIC_ROUTES = ("/", "/documents", "/studio", "/crm", "/analytics")
ADMIN_PROTECTED_ROUTES = ("/api/inventory", "/api/deals", "/api/leads")
SMOKE_DEAL_ID_PREFIX = "smoke-"
SMOKE_EMPTY_DEAL_ID = "smoke-empty-test"


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    status: int | None
    evidence: str
    elapsed_ms: int


def _read_url(base_url: str, path: str, *, timeout: float) -> tuple[int, bytes, str, int]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    started = time.perf_counter()
    request = Request(url, headers={"User-Agent": "tho-production-smoke/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - operator-provided HTTPS URL.
            body = response.read(8 * 1024 * 1024)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return response.status, body, response.headers.get("content-type", ""), elapsed_ms
    except HTTPError as exc:
        body = exc.read(1024 * 1024)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return exc.code, body, exc.headers.get("content-type", ""), elapsed_ms
    except URLError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raise RuntimeError(f"{path} network error: {exc}") from exc


def _post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    admin_token: str | None,
) -> tuple[int, bytes, str, int]:
    """POST JSON to ``path``. Used only by opt-in checks."""
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    body_bytes = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": "tho-production-smoke/1.0",
        "Content-Type": "application/json",
    }
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    request = Request(url, data=body_bytes, headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - operator-provided HTTPS URL.
            body = response.read(8 * 1024 * 1024)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return response.status, body, response.headers.get("content-type", ""), elapsed_ms
    except HTTPError as exc:
        body = exc.read(1024 * 1024)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return exc.code, body, exc.headers.get("content-type", ""), elapsed_ms
    except URLError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raise RuntimeError(f"{path} network error: {exc}") from exc


def _json_probe(base_url: str, path: str, *, timeout: float) -> tuple[int, dict[str, Any], int]:
    status, body, _content_type, elapsed_ms = _read_url(base_url, path, timeout=timeout)
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned non-object JSON")
    return status, payload, elapsed_ms


def check_health(base_url: str, *, timeout: float) -> list[Probe]:
    probes: list[Probe] = []
    for path in ("/health", "/healthz/"):
        status, payload, elapsed_ms = _json_probe(base_url, path, timeout=timeout)
        ok = status == 200 and payload.get("status") == "ok"
        evidence = f"status={payload.get('status')}"
        if path.startswith("/healthz"):
            evidence += f"; version={payload.get('version')}; uptime_s={payload.get('uptime_s')}"
            dependencies = payload.get("dependencies")
            if isinstance(dependencies, dict):
                dependency_text = ",".join(
                    f"{key}:{dependencies[key]}" for key in sorted(dependencies)
                )
                evidence += f"; deps={dependency_text}"
            warnings = payload.get("warnings")
            if isinstance(warnings, list) and warnings:
                evidence += f"; warnings={','.join(str(item) for item in warnings)}"
        probes.append(
            Probe(name=path, ok=ok, status=status, evidence=evidence, elapsed_ms=elapsed_ms)
        )
    return probes


def check_inventory(base_url: str, *, timeout: float, min_homes: int) -> Probe:
    status, payload, elapsed_ms = _json_probe(
        base_url, "/api/marketing/inventory-context", timeout=timeout
    )
    homes = payload.get("homes")
    if not isinstance(homes, list):
        return Probe(
            name="/api/marketing/inventory-context",
            ok=False,
            status=status,
            evidence="homes payload missing or non-list",
            elapsed_ms=elapsed_ms,
        )
    with_prices = sum(1 for home in homes if home.get("display_price"))
    with_media = sum(
        1
        for home in homes
        if home.get("image_url") or home.get("real_photos") or home.get("gallery_images")
    )
    sample_names = ", ".join(str(home.get("model_name") or "unnamed") for home in homes[:3])
    ok = (
        status == 200
        and bool(payload.get("success"))
        and len(homes) >= min_homes
        and with_media > 0
    )
    evidence = (
        f"success={payload.get('success')}; homes={len(homes)}; "
        f"priced={with_prices}; media={with_media}; sample={sample_names}"
    )
    return Probe(
        name="/api/marketing/inventory-context",
        ok=ok,
        status=status,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


def check_spa_routes(base_url: str, *, timeout: float) -> list[Probe]:
    probes: list[Probe] = []
    for path in PUBLIC_ROUTES:
        status, body, content_type, elapsed_ms = _read_url(base_url, path, timeout=timeout)
        text = body[:4096].decode("utf-8", errors="replace").lower()
        has_root = 'id="root"' in text
        ok = status == 200 and "text/html" in content_type and has_root
        probes.append(
            Probe(
                name=path,
                ok=ok,
                status=status,
                evidence=f"content_type={content_type}; root={'yes' if has_root else 'no'}",
                elapsed_ms=elapsed_ms,
            )
        )
    return probes


def check_admin_protection(base_url: str, *, timeout: float) -> list[Probe]:
    probes: list[Probe] = []
    for path in ADMIN_PROTECTED_ROUTES:
        status, body, content_type, elapsed_ms = _read_url(base_url, path, timeout=timeout)
        text = body[:2048].decode("utf-8", errors="replace").lower()
        ok = status in {401, 403} and "html" not in content_type.lower()
        evidence = f"content_type={content_type}; body={text[:90].replace(chr(10), ' ')}"
        probes.append(
            Probe(name=path, ok=ok, status=status, evidence=evidence, elapsed_ms=elapsed_ms)
        )
    return probes


def check_empty_doc_rejection(
    base_url: str,
    *,
    timeout: float,
    admin_token: str | None,
) -> Probe:
    """Opt-in regression probe for the "empty Joe Blo" Document Center bug.

    Posts an empty payload (only a smoke-marked deal id, no buyer/home/etc.)
    to ``/api/documents/generate`` and asserts the API rejects it with a
    400 + structured error envelope. This guards against the regression
    where an empty deal silently produced a 200 with a near-empty PDF
    download URL that looked like a real generated document.

    Side effects: none. The endpoint short-circuits in validation, so no
    PDF is created and no Firestore record is written. The deal id used
    is the reserved ``smoke-empty-test`` so any operator scanning logs
    can recognise the request as a smoke probe.
    """
    payload = {
        "template_name": "TMHA_SalesContract.pdf",
        "data": {"_smoke_deal_id": SMOKE_EMPTY_DEAL_ID},
    }
    status, body, content_type, elapsed_ms = _post_json(
        base_url,
        "/api/documents/generate",
        payload,
        timeout=timeout,
        admin_token=admin_token,
    )
    body_text = body[:4096].decode("utf-8", errors="replace")
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError:
        parsed = {}

    has_download_url = "download_url" in parsed
    is_structured_400 = (
        status == 400
        and parsed.get("error") == "missing_required_fields"
        and parsed.get("success") is False
    )
    ok = is_structured_400 and not has_download_url

    evidence = (
        f"status={status}; content_type={content_type}; "
        f"error={parsed.get('error')!r}; success={parsed.get('success')!r}; "
        f"download_url_present={has_download_url}"
    )
    return Probe(
        name="/api/documents/generate (empty payload)",
        ok=ok,
        status=status,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


def run_smoke(
    base_url: str,
    *,
    timeout: float,
    min_homes: int,
    check_empty_doc: bool = False,
    admin_token: str | None = None,
) -> dict[str, Any]:
    probes: list[Probe] = []
    probes.extend(check_health(base_url, timeout=timeout))
    probes.append(check_inventory(base_url, timeout=timeout, min_homes=min_homes))
    probes.extend(check_spa_routes(base_url, timeout=timeout))
    probes.extend(check_admin_protection(base_url, timeout=timeout))
    if check_empty_doc:
        probes.append(check_empty_doc_rejection(base_url, timeout=timeout, admin_token=admin_token))
    return {
        "ok": all(probe.ok for probe in probes),
        "base_url": base_url.rstrip("/"),
        "probe_count": len(probes),
        "probes": [probe.__dict__ for probe in probes],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--min-homes", type=int, default=40)
    parser.add_argument(
        "--check-empty-doc-rejection",
        action="store_true",
        help=(
            "Opt-in: POST an empty payload to /api/documents/generate and "
            "assert the API rejects it with a 400 + structured error envelope. "
            f"Uses the reserved smoke deal id {SMOKE_EMPTY_DEAL_ID!r} so the "
            "request is never mistaken for a real lead."
        ),
    )
    parser.add_argument(
        "--admin-token",
        default=None,
        help="Admin token used by --check-empty-doc-rejection. Optional.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    result = run_smoke(
        args.base_url,
        timeout=args.timeout,
        min_homes=args.min_homes,
        check_empty_doc=args.check_empty_doc_rejection,
        admin_token=args.admin_token,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
