#!/usr/bin/env python3
"""QA a generated THO PDF packet for obvious production blockers.

This is intentionally lightweight and local: it checks structure, page count,
and extracted text for fake identifiers or unresolved placeholder artifacts.
Use it after downloading a packet from Document Center.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader

PLACEHOLDER_PATTERNS = {
    "fake_hud_or_serial": re.compile(r"\b(?:nta1234567|nta1234568|12345678|12345677)\b", re.I),
    "note_placeholder": re.compile(
        r"(IRREGULA|RREGULAR|01234\s+6789|DDDDDDDDDD|\$\$00000000000000|x{8,}|000000000%)",
        re.I,
    ),
    "template_token": re.compile(r"(\{\{|\}\}|\$\{[^}]+\}|\[object Object\]|\bundefined\b|\bNaN\b)", re.I),
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _missing_expected_texts(combined_text: str, expected_texts: list[str] | None) -> list[str]:
    normalized = _normalize_text(combined_text)
    return [
        expected
        for expected in expected_texts or []
        if _normalize_text(expected) not in normalized
    ]


def qa_pdf(
    path: Path,
    *,
    expected_texts: list[str] | None = None,
    min_pages: int | None = None,
) -> dict[str, Any]:
    reader = PdfReader(str(path))
    text_by_page: list[str] = []
    page_sizes: list[str] = []
    low_text_pages: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text_by_page.append(text)
        page_sizes.append(f"{float(page.mediabox.width):.0f}x{float(page.mediabox.height):.0f}")
        if len(text.strip()) < 20:
            low_text_pages.append(index)

    # pypdf can miss filled AcroForm appearance text after packet merges. When
    # Poppler is installed, use pdftotext as a second pass for field values.
    with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
        try:
            subprocess.run(
                ["pdftotext", "-layout", str(path), tmp.name],
                check=True,
                capture_output=True,
                text=True,
            )
            poppler_pages = Path(tmp.name).read_text(errors="replace").split("\f")
            if len(poppler_pages) >= len(text_by_page):
                text_by_page = [
                    "\n".join([text_by_page[index], poppler_pages[index]])
                    for index in range(len(text_by_page))
                ]
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    findings: list[dict[str, Any]] = []
    for name, pattern in PLACEHOLDER_PATTERNS.items():
        pages: list[int] = []
        count = 0
        for index, text in enumerate(text_by_page, start=1):
            matches = pattern.findall(text)
            if matches:
                pages.append(index)
                count += len(matches)
        if pages:
            findings.append({"code": name, "count": count, "pages": pages})

    if min_pages is not None and len(reader.pages) < min_pages:
        findings.append(
            {
                "code": "too_few_pages",
                "pages": len(reader.pages),
                "expected_min_pages": min_pages,
            }
        )

    missing_expected = _missing_expected_texts("\n".join(text_by_page), expected_texts)
    if missing_expected:
        findings.append(
            {
                "code": "missing_expected_text",
                "missing": missing_expected,
            }
        )

    return {
        "ok": not findings,
        "file": str(path),
        "bytes": path.stat().st_size,
        "pages": len(reader.pages),
        "encrypted": reader.is_encrypted,
        "page_sizes": sorted(set(page_sizes)),
        "low_text_pages": low_text_pages,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="QA a generated THO PDF packet")
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--expect-text",
        action="append",
        default=[],
        help="Require expected text to be extractable from the packet. Repeatable.",
    )
    parser.add_argument("--min-pages", type=int, default=None)
    args = parser.parse_args()

    if not args.pdf.exists():
        print(json.dumps({"ok": False, "error": "file_not_found", "file": str(args.pdf)}, indent=2))
        return 2

    result = qa_pdf(args.pdf, expected_texts=args.expect_text, min_pages=args.min_pages)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
