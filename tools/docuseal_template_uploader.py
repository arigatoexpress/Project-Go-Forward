#!/usr/bin/env python3
"""
Bulk-upload THO PDF templates into a DocuSeal instance.

Usage
-----
  # Dry-run (default) — prints what would happen, no HTTP calls:
  python tools/docuseal_template_uploader.py

  # Actually upload:
  python tools/docuseal_template_uploader.py --apply

  # Re-upload even templates already in the mapping:
  python tools/docuseal_template_uploader.py --apply --force

  # Override env vars via flags:
  python tools/docuseal_template_uploader.py --url https://sign.example.com --token abc123 --apply

Environment variables
---------------------
  DOCUSEAL_API_URL   Base URL of the DocuSeal instance (e.g. https://sign.texashomeoutlet.com)
  DOCUSEAL_API_TOKEN Admin API token obtained from DocuSeal settings
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths (relative to repo root, resolved from this file's location)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = _REPO_ROOT / "tho_documents"
MAPPING_FILE = _REPO_ROOT / "config" / "docuseal_templates.json"
REPORT_DIR = Path(__file__).resolve().parent
DOCUSEAL_UPLOAD_ENDPOINT = "/api/templates/pdf"
RATE_LIMIT_SECONDS = 2


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def load_mapping() -> dict:
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE) as f:
            return json.load(f)
    return {}


def save_mapping(mapping: dict) -> None:
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# DocuSeal API
# ---------------------------------------------------------------------------

def upload_template(pdf_path: Path, api_url: str, api_token: str) -> dict:
    """POST a single PDF to DocuSeal and return the response JSON."""
    url = api_url.rstrip("/") + DOCUSEAL_UPLOAD_ENDPOINT
    with open(pdf_path, "rb") as fh:
        response = requests.post(
            url,
            headers={"X-Auth-Token": api_token},
            files={"file": (pdf_path.name, fh, "application/pdf")},
            timeout=60,
        )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def collect_pdfs() -> list[Path]:
    if not TEMPLATES_DIR.exists():
        print(f"[ERROR] tho_documents/ not found at {TEMPLATES_DIR}", file=sys.stderr)
        sys.exit(1)
    pdfs = sorted(TEMPLATES_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"[ERROR] No PDF files found in {TEMPLATES_DIR}", file=sys.stderr)
        sys.exit(1)
    return pdfs


def run(api_url: str, api_token: str, apply: bool, force: bool) -> None:
    pdfs = collect_pdfs()
    mapping = load_mapping()

    results = []  # list of dicts for report

    for pdf in pdfs:
        name = pdf.name

        if not force and name in mapping:
            results.append({
                "file": name,
                "status": "SKIPPED",
                "template_id": mapping[name].get("docuseal_template_id"),
                "fields_count": mapping[name].get("fields_count"),
                "note": "already in mapping (use --force to re-upload)",
            })
            print(f"  SKIP  {name}  (already mapped → id={mapping[name].get('docuseal_template_id')})")
            continue

        if not apply:
            results.append({
                "file": name,
                "status": "DRY-RUN",
                "template_id": None,
                "fields_count": None,
                "note": "would POST to DocuSeal",
            })
            print(f"  DRY   {name}")
            continue

        # --- live upload ---
        try:
            print(f"  UP    {name} … ", end="", flush=True)
            data = upload_template(pdf, api_url, api_token)

            template_id = data.get("id")
            # DocuSeal returns a list of field schemas; count them
            fields = data.get("fields") or data.get("schema") or []
            fields_count = len(fields) if isinstance(fields, list) else 0

            mapping[name] = {
                "docuseal_template_id": template_id,
                "fields_count": fields_count,
            }
            save_mapping(mapping)

            results.append({
                "file": name,
                "status": "UPLOADED",
                "template_id": template_id,
                "fields_count": fields_count,
                "note": "",
            })
            print(f"id={template_id}  fields={fields_count}")
            time.sleep(RATE_LIMIT_SECONDS)

        except requests.HTTPError as exc:
            results.append({
                "file": name,
                "status": "ERROR",
                "template_id": None,
                "fields_count": None,
                "note": str(exc),
            })
            print(f"ERROR  {exc}")

        except Exception as exc:  # noqa: BLE001
            results.append({
                "file": name,
                "status": "ERROR",
                "template_id": None,
                "fields_count": None,
                "note": str(exc),
            })
            print(f"ERROR  {exc}")

    _write_report(results, apply)
    _print_summary(results)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _write_report(results: list[dict], applied: bool) -> Path:
    today = date.today().isoformat()
    report_path = REPORT_DIR / f".docuseal-upload-report-{today}.md"

    mode_label = "LIVE UPLOAD" if applied else "DRY-RUN"
    lines = [
        f"# DocuSeal Template Upload Report — {today} ({mode_label})",
        "",
        f"| # | File | Status | Template ID | Fields |",
        f"|---|------|--------|-------------|--------|",
    ]
    for i, r in enumerate(results, 1):
        tid = r["template_id"] or "—"
        fc = r["fields_count"] if r["fields_count"] is not None else "—"
        note = f" _{r['note']}_" if r.get("note") else ""
        lines.append(f"| {i} | `{r['file']}` | **{r['status']}** | {tid} | {fc} |{note}")

    lines += [
        "",
        "## Summary",
        "",
    ]
    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    for status, count in sorted(status_counts.items()):
        lines.append(f"- **{status}**: {count}")

    lines += [
        "",
        f"Mapping file: `config/docuseal_templates.json`",
        "",
        "_Generated by `tools/docuseal_template_uploader.py`_",
    ]

    report_path.write_text("\n".join(lines) + "\n")
    print(f"\n[Report] {report_path}")
    return report_path


def _print_summary(results: list[dict]) -> None:
    uploaded = sum(1 for r in results if r["status"] == "UPLOADED")
    skipped = sum(1 for r in results if r["status"] == "SKIPPED")
    dry = sum(1 for r in results if r["status"] == "DRY-RUN")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    print(
        f"\nDone — uploaded={uploaded}  skipped={skipped}  dry-run={dry}  errors={errors}"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Bulk-upload THO PDF templates into DocuSeal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DOCUSEAL_API_URL", ""),
        help="DocuSeal base URL (env: DOCUSEAL_API_URL)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("DOCUSEAL_API_TOKEN", ""),
        help="DocuSeal API token (env: DOCUSEAL_API_TOKEN)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually upload templates (default is dry-run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-upload even templates already present in the mapping",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if args.apply:
        missing = []
        if not args.url:
            missing.append("DOCUSEAL_API_URL (or --url)")
        if not args.token:
            missing.append("DOCUSEAL_API_TOKEN (or --token)")
        if missing:
            print(
                "[ERROR] --apply requires the following to be set:\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\n\nSet them as environment variables or pass --url / --token flags.",
                file=sys.stderr,
            )
            sys.exit(1)

    mode = "LIVE UPLOAD" if args.apply else "DRY-RUN"
    print(f"DocuSeal Template Uploader — mode={mode}")
    print(f"  templates dir : {TEMPLATES_DIR}")
    print(f"  mapping file  : {MAPPING_FILE}")
    if args.apply:
        print(f"  api url       : {args.url}")
    print()

    run(api_url=args.url, api_token=args.token, apply=args.apply, force=args.force)


if __name__ == "__main__":
    main()
