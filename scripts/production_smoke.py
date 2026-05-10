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
from datetime import date, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

try:
    from tools.photo_classifier import is_floorplan_url
except Exception:  # pragma: no cover - smoke fallback for minimal runtimes

    def is_floorplan_url(url: str | None) -> bool:
        if not url or not isinstance(url, str):
            return False
        filename = unquote(url.lower().rsplit("/", 1)[-1].split("?", 1)[0])
        return filename.endswith(".pdf") or any(
            token in filename
            for token in (
                "floorplan",
                "floor-plan",
                "floor_plan",
                "floor-plans",
                "floor_plans",
                "floor plans",
            )
        )


DEFAULT_BASE_URL = "https://tho.sapphirealpha.xyz"
PUBLIC_ROUTES = (
    "/",
    "/inventory",
    "/chat",
    "/contact",
    "/appointments",
    "/documents",
    "/studio",
    "/crm",
    "/analytics",
    "/chat-history",
    "/system",
)
ADMIN_PROTECTED_GET_ROUTES = (
    "/api/inventory",
    "/api/deals",
    "/api/leads",
    "/api/documents/templates",
    "/api/customers/search?q=Smoke&limit=1",
    "/api/analytics/leads?range=30d",
)
ADMIN_PROTECTED_POST_ROUTES = (
    "/api/marketing/generate-script",
    "/api/marketing/schedule",
    "/api/marketing/generate-image",
    "/api/marketing/generate-video",
    "/api/documents/generate-batch",
    "/api/crm/tasks",
)
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


def _as_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value]
    return []


def _real_photo_urls(home: dict[str, Any]) -> list[str]:
    """Return non-floorplan listing photos from a public inventory home."""
    floorplans = {
        str(url).strip().rstrip("/")
        for url in [
            home.get("floorplan_url"),
            home.get("floor_plan_url"),
            *_as_list(home.get("floorplan_urls")),
        ]
        if isinstance(url, str) and url.strip()
    }
    candidates = [
        home.get("image_url"),
        *_as_list(home.get("real_photos")),
        *_as_list(home.get("gallery_images")),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or not isinstance(candidate, str):
            continue
        key = candidate.strip().rstrip("/")
        if key in seen or key in floorplans or is_floorplan_url(candidate):
            continue
        seen.add(key)
        out.append(candidate)
    return out


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
    with_media = sum(1 for home in homes if _real_photo_urls(home))
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


def check_inventory_media_depth(base_url: str, *, timeout: float) -> Probe:
    status, payload, elapsed_ms = _json_probe(
        base_url, "/api/marketing/inventory-context", timeout=timeout
    )
    homes = payload.get("homes")
    if not isinstance(homes, list):
        return Probe(
            name="/api/marketing/inventory-context media depth",
            ok=False,
            status=status,
            evidence="homes payload missing or non-list",
            elapsed_ms=elapsed_ms,
        )
    real_photo_rich = sum(1 for home in homes if len(_real_photo_urls(home)) >= 3)
    gallery_rich = sum(
        1
        for home in homes
        if len([url for url in _as_list(home.get("gallery_images")) if isinstance(url, str)]) >= 3
        and len(_real_photo_urls(home)) >= 3
    )
    matterport = sum(1 for home in homes if home.get("matterport_url"))
    dealer_photo_sets = sum(
        1
        for home in homes
        if any("/dealer/3522/inventory/" in str(url) for url in _real_photo_urls(home))
    )
    ok = status == 200 and real_photo_rich >= 30 and gallery_rich >= 30 and matterport >= 20
    evidence = (
        f"real_photo_rich={real_photo_rich}; gallery_rich={gallery_rich}; "
        f"matterport={matterport}; dealer_photo_sets={dealer_photo_sets}"
    )
    return Probe(
        name="/api/marketing/inventory-context media depth",
        ok=ok,
        status=status,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


def _future_business_date() -> str:
    candidate = date.today() + timedelta(days=1)
    while candidate.weekday() == 6:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def check_public_helpers(base_url: str, *, timeout: float) -> list[Probe]:
    probes: list[Probe] = []

    slots_path = f"/api/appointments/slots?date={_future_business_date()}"
    status, payload, elapsed_ms = _json_probe(base_url, slots_path, timeout=timeout)
    slots = payload.get("available_slots")
    ok = status == 200 and isinstance(slots, list) and len(slots) > 0
    probes.append(
        Probe(
            name="/api/appointments/slots",
            ok=ok,
            status=status,
            evidence=(
                f"date={payload.get('date')}; "
                f"slots={len(slots) if isinstance(slots, list) else 'missing'}"
            ),
            elapsed_ms=elapsed_ms,
        )
    )

    status, payload, elapsed_ms = _json_probe(
        base_url, "/api/admin/passkey/status", timeout=timeout
    )
    ok = (
        status == 200
        and payload.get("enabled") is True
        and payload.get("persistent") is True
        and "sapphirealpha.xyz" in (payload.get("rp_ids") or [])
    )
    probes.append(
        Probe(
            name="/api/admin/passkey/status",
            ok=ok,
            status=status,
            evidence=(
                f"enabled={payload.get('enabled')}; persistent={payload.get('persistent')}; "
                f"has_keys={payload.get('has_keys')}; rp_ids={payload.get('rp_ids')}"
            ),
            elapsed_ms=elapsed_ms,
        )
    )
    return probes


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


def check_safe_public_validation(base_url: str, *, timeout: float) -> list[Probe]:
    probes: list[Probe] = []
    checks = (
        ("/api/contact", {}, {200}, "success_false"),
        ("/api/appointments", {}, {200}, "success_false"),
        ("/api/feedback", {}, {400}, "validation_error"),
    )
    for path, payload, allowed_statuses, mode in checks:
        status, body, content_type, elapsed_ms = _post_json(
            base_url,
            path,
            payload,
            timeout=timeout,
            admin_token=None,
        )
        text = body[:2048].decode("utf-8", errors="replace")
        parsed: dict[str, Any] = {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        if mode == "success_false":
            ok = status in allowed_statuses and parsed.get("success") is False
        else:
            ok = status in allowed_statuses and (
                parsed.get("success") is False or "description is required" in text.lower()
            )
        evidence = (
            f"content_type={content_type}; success={parsed.get('success')}; "
            f"message={parsed.get('message') or parsed.get('error')}"
        )
        probes.append(
            Probe(
                name=f"{path} invalid payload",
                ok=ok,
                status=status,
                evidence=evidence,
                elapsed_ms=elapsed_ms,
            )
        )
    return probes


def check_admin_protection(base_url: str, *, timeout: float) -> list[Probe]:
    probes: list[Probe] = []
    for path in ADMIN_PROTECTED_GET_ROUTES:
        status, body, content_type, elapsed_ms = _read_url(base_url, path, timeout=timeout)
        text = body[:2048].decode("utf-8", errors="replace").lower()
        ok = status in {401, 403} and "html" not in content_type.lower()
        evidence = f"content_type={content_type}; body={text[:90].replace(chr(10), ' ')}"
        probes.append(
            Probe(name=path, ok=ok, status=status, evidence=evidence, elapsed_ms=elapsed_ms)
        )
    for path in ADMIN_PROTECTED_POST_ROUTES:
        status, body, content_type, elapsed_ms = _post_json(
            base_url,
            path,
            {},
            timeout=timeout,
            admin_token=None,
        )
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
    probes.append(check_inventory_media_depth(base_url, timeout=timeout))
    probes.extend(check_public_helpers(base_url, timeout=timeout))
    probes.extend(check_spa_routes(base_url, timeout=timeout))
    probes.extend(check_safe_public_validation(base_url, timeout=timeout))
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
