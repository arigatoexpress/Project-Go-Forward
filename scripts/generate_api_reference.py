#!/usr/bin/env python3
"""Generate docs/API_REFERENCE.md from the app's own OpenAPI schema.

Usage (from the repo root):

    python scripts/generate_api_reference.py [--output docs/API_REFERENCE.md]

The script imports ``main.py`` to build the FastAPI app, calls
``app.openapi()``, and renders a compact, deterministic Markdown reference
grouped by OpenAPI tag (untagged routes are grouped by path prefix).

Importing ``main.py`` requires ``ADMIN_PIN_HASH`` to be set. If it is absent
this script sets it to the PUBLIC test fixture from ``tests/conftest.py``
(sha256 of the string "4832") — that value is committed to the repo and is
not an operator secret. ``ENVIRONMENT`` defaults to ``development`` so the
OpenAPI/docs endpoints stay enabled locally. No real secrets are read,
printed, or embedded in the output; only environment-variable NAMES may be
mentioned.

Output is deterministic: groups and endpoints are sorted, and no timestamps
or machine-specific values are written, so re-running on unchanged code
produces a byte-identical document.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "API_REFERENCE.md"

GENERATED_MARKER = "<!-- GENERATED FILE — do not edit by hand -->"

# HTTP methods FastAPI can emit in an OpenAPI path item, in render order.
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")

# Friendly labels for the auth dependencies that are statically visible on
# routes. Anything not listed here is rendered by its function name.
_AUTH_LABELS = {
    "require_admin": "Admin session (PIN or email code)",
    "require_partner_api_key": "Partner API key",
    "_require_passkey_user": "Passkey session",
}

# Dependencies that are infrastructure (DI wiring), not auth — omit from docs.
_NON_AUTH_DEPENDENCIES = {"get_credential_store", "get_session_manager"}

# Groups for untagged routes that do not start with /api/.
_PUBLIC_FILE_ROUTES = {"/robots.txt", "/sitemap.xml", "/llms.txt"}
_HEALTH_ROUTES = {
    "/health",
    "/healthz",
    "/healthz/",
    "/healthz/detailed",
    "/readyz",
    "/readyz/",
    "/api/metrics",
}


def _prepare_environment() -> None:
    """Set the minimal env needed to import main.py (no operator secrets)."""
    # Public test fixture from tests/conftest.py — NOT a production secret.
    os.environ.setdefault("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
    os.environ.setdefault("ENVIRONMENT", "development")

    # main.py mounts StaticFiles("frontend/dist/assets") at import time and
    # raises if the directory is absent (fresh checkout / worktree that never
    # ran the Vite build). Mirror tests/conftest.py and create a stub.
    dist = REPO_ROOT / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = dist / "index.html"
    if not index_html.exists():
        index_html.write_text('<html><body id="root">stub</body></html>', encoding="utf-8")


def _load_app():
    """Import main.py from the repo root and return the FastAPI app."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import main  # noqa: E402 — import must follow env prep

    return main.app


def _auth_map(app) -> dict[tuple[str, str], list[str]]:
    """Map (path, METHOD) -> human-readable auth labels from route dependencies."""
    from fastapi.routing import APIRoute

    mapping: dict[tuple[str, str], list[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        labels: list[str] = []
        for dep in route.dependant.dependencies:
            call = dep.call
            name = getattr(call, "__name__", None)
            if not name or name in _NON_AUTH_DEPENDENCIES:
                continue
            label = _AUTH_LABELS.get(name, name)
            if label not in labels:
                labels.append(label)
        for method in route.methods or ():
            mapping[(route.path, method.upper())] = labels
    return mapping


def _schema_name(schema: dict) -> str | None:
    """Return the component schema name for a $ref, else None."""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref", "")
    if ref.startswith("#/components/schemas/"):
        return ref.rsplit("/", 1)[-1]
    return None


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _params_cell(op: dict) -> str:
    path_params: list[str] = []
    query_params: list[str] = []
    for param in op.get("parameters") or []:
        name = param.get("name", "")
        if not name:
            continue
        if param.get("required"):
            name = f"*{name}*"
        if param.get("in") == "path":
            path_params.append(name)
        elif param.get("in") == "query":
            query_params.append(name)
    parts = []
    if path_params:
        parts.append("path: " + ", ".join(sorted(path_params)))
    if query_params:
        parts.append("query: " + ", ".join(sorted(query_params)))
    return "; ".join(parts) if parts else "—"


def _request_body_cell(op: dict) -> str:
    content = (op.get("requestBody") or {}).get("content") or {}
    for media in content.values():
        name = _schema_name(media.get("schema") or {})
        if name:
            return name
    return "—"


def _responses_cell(op: dict) -> str:
    parts: list[str] = []
    for status in sorted((op.get("responses") or {}).keys()):
        if status == "422":  # FastAPI validation error boilerplate
            continue
        content = (op["responses"][status].get("content") or {})
        name = None
        for media in content.values():
            name = _schema_name(media.get("schema") or {})
            if name:
                break
        parts.append(f"{status}: {name}" if name else status)
    return ", ".join(parts) if parts else "—"


def _group_key(path: str, tags: list[str]) -> str:
    """Deterministic group title for one path."""
    if tags:
        return f"Tag: `{tags[0]}`"
    if path in _HEALTH_ROUTES:
        return "Health & metrics"
    if path in _PUBLIC_FILE_ROUTES:
        return "SEO & public files"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "Other"
    if segments[0] == "api" and len(segments) >= 2:
        return f"`/api/{segments[1]}`"
    return f"`/{segments[0]}`"


def render_markdown(app) -> str:
    """Render the API reference Markdown for a FastAPI app (no file I/O)."""
    spec = app.openapi()
    auth = _auth_map(app)

    groups: dict[str, list[tuple[str, str, dict]]] = {}
    total_ops = 0
    for path in sorted(spec.get("paths", {})):
        item = spec["paths"][path]
        for method in _HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            total_ops += 1
            key = _group_key(path, op.get("tags") or [])
            groups.setdefault(key, []).append((path, method.upper(), op))

    lines: list[str] = [
        GENERATED_MARKER,
        "",
        "# API Reference",
        "",
        "Generated by `scripts/generate_api_reference.py` from the app's own OpenAPI schema "
        "(`app.openapi()`). Do not edit by hand — regenerate with:",
        "",
        "```bash",
        "python scripts/generate_api_reference.py",
        "```",
        "",
        f"Covers {len(spec.get('paths', {}))} paths / {total_ops} operations. "
        "Auth hints are read statically from route dependencies; "
        "mutating admin endpoints additionally require the CSRF header issued at login "
        "(see docs/SECURITY.md). The interactive OpenAPI UI (`/docs`) is disabled in "
        "deployed environments; this document is the committed reference.",
        "",
        "In tables: *italic* parameters are required; `—` means none/unspecified.",
        "",
    ]

    def _sort_key(group_title: str) -> tuple[int, str]:
        # Tagged groups first (sorted by tag), then untagged prefix groups.
        return (0 if group_title.startswith("Tag: ") else 1, group_title.lower())

    for group_title in sorted(groups, key=_sort_key):
        lines.append(f"## {group_title}")
        lines.append("")
        lines.append("| Method | Path | Summary | Auth | Parameters | Request body | Responses |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for path, method, op in sorted(groups[group_title], key=lambda e: (e[0], e[1])):
            summary = _escape_cell(_first_line(op.get("summary") or op.get("description")))
            auth_labels = auth.get((path, method), [])
            auth_cell = ", ".join(auth_labels) if auth_labels else "public"
            row = (
                f"| {method} | `{path}` | {summary or '—'} | {_escape_cell(auth_cell)} "
                f"| {_escape_cell(_params_cell(op))} | {_request_body_cell(op)} "
                f"| {_escape_cell(_responses_cell(op))} |"
            )
            lines.append(row)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def generate(output_path: Path = DEFAULT_OUTPUT) -> Path:
    """Build the app, render Markdown, and write it to output_path."""
    _prepare_environment()
    app = _load_app()
    markdown = render_markdown(app)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Where to write the Markdown (default: docs/API_REFERENCE.md).",
    )
    args = parser.parse_args(argv)
    try:
        path = generate(args.output)
    except Exception as exc:  # noqa: BLE001 — CLI should report any failure
        print(f"error: failed to generate API reference: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
