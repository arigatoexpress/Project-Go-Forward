#!/usr/bin/env python3
# ruff: noqa: E402
"""Run the THO local-SEO competitor audit from the command line.

Usage:
    python scripts/local_seo_audit.py
    python scripts/local_seo_audit.py --registry data/competitors.json
    python scripts/local_seo_audit.py --output /tmp/local_seo_audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.local_seo_competitor import analyze_local_seo


def main() -> int:
    parser = argparse.ArgumentParser(
        description="THO local SEO competitor audit",
    )
    parser.add_argument(
        "--registry",
        help="Path to a custom competitor JSON registry",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Write JSON report to this file",
    )
    args = parser.parse_args()

    report = analyze_local_seo(path=args.registry)
    print(json.dumps(report, indent=2))

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nReport written to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
