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
import re
import sys
from pathlib import Path
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

# Make the repo root importable when this file is run directly
# (`python scripts/production_smoke.py`). Without it the import below failed
# silently and the smoke fell back to a filename-only classifier that cannot see
# content-flagged floorplans — reporting floorplan_heroes=0 while the live site
# served 33 of them. A degraded check that stays green is worse than no check.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CLASSIFIER_FULL = True
try:
    from tools.photo_classifier import is_floorplan_url
except Exception:  # pragma: no cover - smoke fallback for minimal runtimes
    CLASSIFIER_FULL = False

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


DEFAULT_BASE_URL = "https://www.texashomeoutlet.com"
DEFAULT_MIN_HOMES = 10
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

# A harmless prompt for the opt-in /run correctness probe. It exercises the live
# ADK agent reply path; a re-introduced prompt-strip (#223) makes /run return an
# error envelope, which the probe asserts against. The session id is anonymous
# so the reply never binds to a real customer record.
SMOKE_RUN_MESSAGE = "Hello, are any homes available?"
# An obviously-wrong PIN for the admin-auth liveness probe. We never send a real
# secret: a well-formed 401 (or 429 lockout) proves the endpoint + rate-limit
# path are alive without authenticating.
SMOKE_WRONG_PIN = "0000-smoke-not-a-real-pin"


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
    except TimeoutError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raise RuntimeError(f"{path} timed out after {timeout:g}s") from exc
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
    except TimeoutError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raise RuntimeError(f"{path} timed out after {timeout:g}s") from exc
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
        f"min_homes={min_homes}; priced={with_prices}; media={with_media}; sample={sample_names}"
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
    media_target_homes = [
        home for home in homes if home.get("inventory_kind") != "orderable_floorplan"
    ] or homes
    real_photo_rich = sum(1 for home in media_target_homes if len(_real_photo_urls(home)) >= 3)
    gallery_rich = sum(
        1
        for home in media_target_homes
        if len([url for url in _as_list(home.get("gallery_images")) if isinstance(url, str)]) >= 3
        and len(_real_photo_urls(home)) >= 3
    )
    matterport = sum(1 for home in homes if home.get("matterport_url"))
    dealer_photo_sets = sum(
        1
        for home in media_target_homes
        if any("/dealer/3522/inventory/" in str(url) for url in _real_photo_urls(home))
    )
    total = len(media_target_homes)
    rich_required = min(30, max(1, total))
    matterport_required = min(20, max(1, total // 3))
    ok = (
        status == 200
        and real_photo_rich >= rich_required
        and gallery_rich >= rich_required
        and matterport >= matterport_required
    )
    evidence = (
        f"real_photo_rich={real_photo_rich}; gallery_rich={gallery_rich}; "
        f"matterport={matterport}; dealer_photo_sets={dealer_photo_sets}; "
        f"media_target_homes={total}; required_rich={rich_required}; "
        f"required_matterport={matterport_required}"
    )
    return Probe(
        name="/api/marketing/inventory-context media depth",
        ok=ok,
        status=status,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


def _head_status(url: str, *, timeout: float = 15.0) -> int:
    """HEAD a media URL and return its status (0 when unreachable)."""
    try:
        request = Request(url, method="HEAD")
        with urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def evaluate_served_commit(
    *, served: str | None, expected: str | None, status: int, elapsed_ms: int
) -> Probe:
    """Fail when production is not serving the commit you merged.

    Production pins traffic (`--no-traffic --tag=candidate` in deploy.yml), so a
    merge alone never promotes a revision — drift is the DEFAULT state here, not
    an edge case. Twice on 2026-08-31 a cutover was believed complete while the
    live bundle still carried pre-fix code, and nothing anywhere compared what is
    DEPLOYED to what is MERGED.

    Short SHAs are accepted on either side so a 7-char expectation matches.
    """
    served = (served or "").strip()
    if not served:
        return Probe(
            name="production serves the merged commit",
            ok=False,
            status=status,
            evidence="deployed commit unknown (/healthz/ returned no version)",
            elapsed_ms=elapsed_ms,
        )
    if not expected:
        return Probe(
            name="production serves the merged commit",
            ok=True,
            status=status,
            evidence=f"serving {served[:7]}; no expected commit supplied (--expect-commit)",
            elapsed_ms=elapsed_ms,
        )
    expected = expected.strip()
    width = min(len(served), len(expected))
    match = served[:width].lower() == expected[:width].lower()
    return Probe(
        name="production serves the merged commit",
        ok=status == 200 and match,
        status=status,
        evidence=(
            f"serving={served[:7]}; expected={expected[:7]}; "
            + ("match" if match else "DRIFT — production is not running the merged code")
        ),
        elapsed_ms=elapsed_ms,
    )


def evaluate_floorplan_heroes(
    homes: list[dict[str, Any]], *, status: int, elapsed_ms: int
) -> Probe:
    """Fail when any listing leads with a floorplan drawing.

    `check_inventory_media_depth` counts photo URLs, so a floorplan counted as a
    photo and the probe stayed green while the site's FEATURED home was a
    floorplan drawing (2026-08-31). This asks the classifier what the hero
    actually IS.
    """
    offenders: list[str] = []
    for home in homes:
        hero = home.get("image_url")
        if not hero or not isinstance(hero, str):
            continue  # photo-less homes render a branded placeholder by design
        if is_floorplan_url(hero):
            offenders.append(str(home.get("id") or home.get("model_name") or "?"))
    # Fail CLOSED on a degraded classifier. Without the content manifest this
    # probe cannot see the floorplans it exists to catch, and a green result
    # would be a lie.
    return Probe(
        name="inventory heroes are photos, not floorplans",
        ok=status == 200 and not offenders and CLASSIFIER_FULL,
        status=status,
        evidence=(
            f"floorplan_heroes={len(offenders)}; ids={','.join(offenders[:8]) or 'none'}; "
            f"classifier={'full' if CLASSIFIER_FULL else 'DEGRADED-filename-only'}"
        ),
        elapsed_ms=elapsed_ms,
    )


def evaluate_hero_reachability(
    homes: list[dict[str, Any]],
    *,
    status: int,
    elapsed_ms: int,
    fetch_status: Callable[[str], int],
    max_dead: int = 0,
    sample: int | None = None,
) -> Probe:
    """Fail when hero images do not actually load.

    A URL in a JSON array is not a picture. 22 heroes answered 403 from the
    vendor CDN while every existing probe stayed green, because nothing ever
    fetched them.
    """
    heroes = [
        (str(home.get("id") or "?"), home["image_url"])
        for home in homes
        if isinstance(home.get("image_url"), str) and home["image_url"].strip()
    ]
    if sample:
        heroes = heroes[:sample]
    dead: list[str] = []
    for home_id, url in heroes:
        code = fetch_status(url)
        if code != 200:
            dead.append(f"{home_id}:{code}:{url.rsplit('/', 1)[-1][:40]}")
    return Probe(
        name="inventory hero images are reachable",
        ok=status == 200 and len(dead) <= max_dead,
        status=status,
        evidence=(
            f"checked={len(heroes)}; dead={len(dead)}; allowed={max_dead}; "
            f"{'; '.join(dead[:6]) or 'all reachable'}"
        ),
        elapsed_ms=elapsed_ms,
    )


def check_inventory_media_reality(
    base_url: str, *, timeout: float, hero_sample: int, max_dead_heroes: int
) -> list[Probe]:
    """Assert what the catalog SHOWS, not merely what it lists.

    Fetched once and shared by both probes so a full smoke stays cheap.
    """
    status, payload, elapsed_ms = _json_probe(
        base_url, "/api/marketing/inventory-context", timeout=timeout
    )
    homes = [home for home in _as_list(payload.get("homes")) if isinstance(home, dict)]
    return [
        evaluate_floorplan_heroes(homes, status=status, elapsed_ms=elapsed_ms),
        evaluate_hero_reachability(
            homes,
            status=status,
            elapsed_ms=elapsed_ms,
            fetch_status=lambda url: _head_status(url, timeout=timeout),
            max_dead=max_dead_heroes,
            sample=hero_sample,
        ),
    ]


def check_served_commit(base_url: str, *, timeout: float, expected: str | None) -> Probe:
    status, payload, elapsed_ms = _json_probe(base_url, "/healthz/", timeout=timeout)
    served = payload.get("version") if isinstance(payload, dict) else ""
    return evaluate_served_commit(
        served=served if isinstance(served, str) else "",
        expected=expected,
        status=status,
        elapsed_ms=elapsed_ms,
    )


def check_canonical_authority(
    base_url: str, *, timeout: float, canonical_origin: str | None = None
) -> Probe:
    """Fail when machine-readable SEO surfaces advertise another domain.

    Candidate ``*.run.app`` storefront pages deliberately redirect to the
    production domain, so their canonical tags cannot be inspected without
    accidentally testing the old live revision. For those hosts, robots.txt
    and sitemap.xml are the direct candidate contract; unit tests cover the
    homepage tag generated from the same canonical-base function.
    """
    origin = (canonical_origin or base_url).rstrip("/")
    candidate_host = (urlsplit(base_url).hostname or "").endswith(".run.app")
    robots_status, robots_body, robots_type, robots_ms = _read_url(
        base_url, "/robots.txt", timeout=timeout
    )
    sitemap_status, sitemap_body, sitemap_type, sitemap_ms = _read_url(
        base_url, "/sitemap.xml", timeout=timeout
    )
    robots_text = robots_body.decode("utf-8", errors="replace")
    sitemap_text = sitemap_body.decode("utf-8", errors="replace")
    robots_ok = f"Sitemap: {origin}/sitemap.xml" in robots_text
    sitemap_ok = f"<loc>{origin}/" in sitemap_text

    homepage_ok = True
    homepage_evidence = "machine-path-proxy"
    home_status = 200
    home_ms = 0
    if not candidate_host:
        home_status, home_body, home_type, home_ms = _read_url(base_url, "/", timeout=timeout)
        home_text = home_body.decode("utf-8", errors="replace")
        canonical_match = re.search(
            r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)',
            home_text,
            flags=re.IGNORECASE,
        )
        advertised = canonical_match.group(1).rstrip("/") if canonical_match else ""
        homepage_ok = home_status == 200 and "html" in home_type.lower() and advertised == origin
        homepage_evidence = "yes" if homepage_ok else "no"

    ok = (
        homepage_ok
        and robots_status == 200
        and sitemap_status == 200
        and "text/plain" in robots_type.lower()
        and "xml" in sitemap_type.lower()
        and robots_ok
        and sitemap_ok
    )
    status = next(
        (code for code in (home_status, robots_status, sitemap_status) if code != 200),
        200,
    )
    return Probe(
        name="canonical search authority",
        ok=ok,
        status=status,
        evidence=(
            f"homepage={homepage_evidence}; robots={'yes' if robots_ok else 'no'}; "
            f"sitemap={'yes' if sitemap_ok else 'no'}; expected={origin}"
        ),
        elapsed_ms=home_ms + robots_ms + sitemap_ms,
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
        # Scan the whole shell, not just the first 4 KB: rich server-rendered
        # JSON-LD (LocalBusiness + ItemList) now pushes <div id="root"> past byte
        # ~8.5 KB on / and /inventory, so a 4 KB window false-failed the deploy.
        text = body.decode("utf-8", errors="replace").lower()
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


def check_run_reply(base_url: str, *, timeout: float) -> Probe:
    """Opt-in /run correctness probe — asserts a NON-ERROR agent reply.

    This is the gate that catches a re-introduced prompt-strip (#223). When the
    prompts/*.md templates are missing from the image the ADK runner cannot init
    and /run returns ``{"error": ...}`` with a 200. A liveness probe stays green
    through that outage; this probe goes RED.

    Success = HTTP 200 + a non-empty ``text`` field + NO ``error`` field. Posts
    an anonymous session so the reply never binds to a real customer record.
    """
    payload = {
        "userId": "smoke-probe",
        "sessionId": "smoke-probe-session",
        "newMessage": {"role": "user", "parts": [{"text": SMOKE_RUN_MESSAGE}]},
    }
    status, body, content_type, elapsed_ms = _post_json(
        base_url,
        "/run",
        payload,
        timeout=timeout,
        admin_token=None,
    )
    body_text = body[:4096].decode("utf-8", errors="replace")
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError:
        parsed = {}

    has_error = "error" in parsed
    reply_text = parsed.get("text")
    has_reply = isinstance(reply_text, str) and bool(reply_text.strip())
    ok = status == 200 and not has_error and has_reply

    reply_preview = (
        (reply_text or "")[:60].replace("\n", " ") if isinstance(reply_text, str) else ""
    )
    evidence = (
        f"status={status}; content_type={content_type}; "
        f"has_error={has_error}; error={parsed.get('error')!r}; "
        f"reply_len={len(reply_text) if isinstance(reply_text, str) else 0}; "
        f"reply={reply_preview!r}"
    )
    return Probe(
        name="/run (agent reply)",
        ok=ok,
        status=status,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


def check_admin_auth_liveness(base_url: str, *, timeout: float) -> Probe:
    """Opt-in admin-auth liveness probe — wrong PIN must yield a clean 401.

    Verifies ``/api/admin/verify`` and its rate-limit/lockout path are ALIVE
    without needing the real secret: we POST an obviously-wrong PIN and assert a
    well-formed ``{"success": false, ...}`` 401 (a 429 lockout is also healthy).
    An unexpected 200 would mean auth is broken/open. Safe + idempotent: a wrong
    PIN writes nothing but a failed-attempt counter that ages out.
    """
    payload = {"pin": SMOKE_WRONG_PIN}
    status, body, content_type, elapsed_ms = _post_json(
        base_url,
        "/api/admin/verify",
        payload,
        timeout=timeout,
        admin_token=None,
    )
    body_text = body[:2048].decode("utf-8", errors="replace")
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError:
        parsed = {}

    # 401 = correct rejection of a wrong PIN; 429 = lockout (rate-limit alive).
    well_formed = parsed.get("success") is False and "html" not in content_type.lower()
    ok = status in {401, 429} and well_formed
    evidence = (
        f"status={status}; content_type={content_type}; "
        f"success={parsed.get('success')!r}; error={parsed.get('error')!r}"
    )
    return Probe(
        name="/api/admin/verify (wrong PIN -> 401)",
        ok=ok,
        status=status,
        evidence=evidence,
        elapsed_ms=elapsed_ms,
    )


# Stable aliases so run_smoke can invoke the probes even though its boolean
# parameters (check_run_reply / check_admin_auth) intentionally share the names
# for a clean CLI surface and would otherwise shadow the functions in scope.
_run_reply_probe = check_run_reply
_admin_auth_probe = check_admin_auth_liveness


def run_smoke(
    base_url: str,
    *,
    timeout: float,
    min_homes: int,
    check_empty_doc: bool = False,
    check_run_reply: bool = False,
    check_admin_auth: bool = False,
    admin_token: str | None = None,
    canonical_origin: str | None = None,
    hero_sample: int = 40,
    max_dead_heroes: int = 0,
    expect_commit: str | None = None,
) -> dict[str, Any]:
    probes: list[Probe] = []
    probes.extend(check_health(base_url, timeout=timeout))
    probes.append(check_served_commit(base_url, timeout=timeout, expected=expect_commit))
    probes.append(check_inventory(base_url, timeout=timeout, min_homes=min_homes))
    probes.append(check_inventory_media_depth(base_url, timeout=timeout))
    probes.extend(
        check_inventory_media_reality(
            base_url,
            timeout=timeout,
            hero_sample=hero_sample,
            max_dead_heroes=max_dead_heroes,
        )
    )
    probes.append(
        check_canonical_authority(base_url, timeout=timeout, canonical_origin=canonical_origin)
    )
    probes.extend(check_public_helpers(base_url, timeout=timeout))
    probes.extend(check_spa_routes(base_url, timeout=timeout))
    probes.extend(check_safe_public_validation(base_url, timeout=timeout))
    probes.extend(check_admin_protection(base_url, timeout=timeout))
    if check_empty_doc:
        probes.append(check_empty_doc_rejection(base_url, timeout=timeout, admin_token=admin_token))
    if check_run_reply:
        probes.append(_run_reply_probe(base_url, timeout=timeout))
    if check_admin_auth:
        probes.append(_admin_auth_probe(base_url, timeout=timeout))
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
    parser.add_argument("--min-homes", type=int, default=DEFAULT_MIN_HOMES)
    parser.add_argument(
        "--expect-commit",
        default=None,
        help="Fail if production is not serving this commit SHA (short or full).",
    )
    parser.add_argument(
        "--hero-sample",
        type=int,
        default=40,
        help="How many hero images to actually fetch (0 = all).",
    )
    parser.add_argument(
        "--max-dead-heroes",
        type=int,
        default=0,
        help="Dead hero images tolerated before the smoke fails.",
    )
    parser.add_argument(
        "--canonical-origin",
        default=None,
        help=(
            "Expected public search origin when --base-url is an isolated "
            "candidate or staging hostname."
        ),
    )
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
        "--check-run-reply",
        action="store_true",
        help=(
            "Opt-in: POST a harmless prompt to /run and assert a non-error agent "
            "reply (200 with a non-empty text field, no error envelope). Catches a "
            "re-introduced prompt-strip (#223) that a liveness probe would miss."
        ),
    )
    parser.add_argument(
        "--check-admin-auth",
        action="store_true",
        help=(
            "Opt-in: POST an obviously-wrong PIN to /api/admin/verify and assert a "
            "well-formed 401 (or 429 lockout). Verifies the auth + rate-limit path "
            "are alive WITHOUT needing the real secret."
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
        check_run_reply=args.check_run_reply,
        check_admin_auth=args.check_admin_auth,
        admin_token=args.admin_token,
        canonical_origin=args.canonical_origin,
        hero_sample=args.hero_sample or None,
        max_dead_heroes=args.max_dead_heroes,
        expect_commit=args.expect_commit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
