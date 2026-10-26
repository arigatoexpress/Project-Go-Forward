#!/usr/bin/env python3
"""Target and high-severity gate for the public OWASP ZAP baseline.

The scan target is the public production origin already used when a deploy is
verified. It is the ``--canonical-origin`` value in
``.github/workflows/deploy.yml``. The job refuses to scan unless that origin
also matches ``DEFAULT_BASE_URL`` in ``scripts/production_smoke.py`` and
``business.website`` in ``config.yaml``. Candidate tag hosts, other Cloud Run
hosts, and any URL with a path or query are rejected.

``gate`` reads a ZAP baseline JSON report and exits 1 only when a high-severity
alert remains after the rules file. Medium, low, and informational alerts are
printed and do not fail the run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
SMOKE_SCRIPT = Path("scripts/production_smoke.py")
BUSINESS_CONFIG = Path("config.yaml")

_LEVELS = frozenset({"IGNORE", "INFO", "WARN", "FAIL", "OUTOFSCOPE"})
_ORIGIN = re.compile(r"https://[a-z0-9.-]+")
_HOST = re.compile(r"[a-z0-9.-]+")


class TargetRejected(ValueError):
    """The deploy/verify config does not name one public production origin."""


def _read(repo: Path, relative: Path) -> str:
    path = repo / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TargetRejected(f"Could not read {relative.as_posix()}: {exc}") from exc


def _one(pattern: str, text: str, label: str) -> str:
    found = re.findall(pattern, text, flags=re.MULTILINE)
    if len(found) != 1:
        raise TargetRejected(f"Expected exactly one {label}, found {len(found)}")
    return found[0]


def normalize_origin(value: str) -> str:
    """Return ``https://host`` or reject anything that is not that origin."""
    raw = value.strip()
    if not raw or any(character.isspace() for character in raw):
        raise TargetRejected("Origin must be a single https URL")
    if any(character in raw for character in "\"'`$|&;<>(){}[]!*\\"):
        raise TargetRejected("Origin contains characters that cannot be scanned")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme != "https"
        or not host
        or parts.username
        or parts.password
        or parts.port is not None
    ):
        raise TargetRejected("Origin must be https with a host and no credentials or port")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise TargetRejected("Origin must be the site root, with no path, query, or fragment")
    if not _HOST.fullmatch(host) or "." not in host or host.startswith(".") or host.endswith("."):
        raise TargetRejected("Origin host is not a public DNS name")
    if host in {"localhost", "127.0.0.1"} or host.endswith(".run.app") or "candidate---" in host:
        raise TargetRejected("Refusing a candidate, Cloud Run, or loopback host")
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        raise TargetRejected("Refusing a raw IP address")
    origin = f"https://{host}"
    if not _ORIGIN.fullmatch(origin):
        raise TargetRejected("Origin is not a plain https URL")
    return origin


def production_origin(repo: Path | None = None) -> str:
    """Return the one public production origin named by deploy verification."""
    root = repo or REPO
    deploy = _one(
        r'--canonical-origin\s+"([^"]+)"',
        _read(root, DEPLOY_WORKFLOW),
        "deploy verify --canonical-origin",
    )
    smoke = _one(
        r'^DEFAULT_BASE_URL\s*=\s*"([^"]+)"',
        _read(root, SMOKE_SCRIPT),
        "production smoke DEFAULT_BASE_URL",
    )
    website = _one(
        r'^  website:\s*"([^"]+)"\s*$',
        _read(root, BUSINESS_CONFIG),
        "config.yaml business.website",
    )
    origins = {
        "deploy verify canonical origin": normalize_origin(deploy),
        "production smoke default": normalize_origin(smoke),
        "config.yaml business.website": normalize_origin(website),
    }
    unique = set(origins.values())
    if len(unique) != 1:
        detail = ", ".join(f"{name}={value}" for name, value in origins.items())
        raise TargetRejected(f"Deploy/verify production origins disagree: {detail}")
    return next(iter(unique))


def load_rules(path: Path) -> dict[str, str]:
    """Return plugin id to ZAP level. Comment lines start with ``#``."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read rules file: {exc}") from exc
    rules: dict[str, str] = {}
    previous = ""
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            previous = stripped
            continue
        if not previous.startswith("#"):
            raise ValueError(f"{path}:{line_no}: rule is missing a comment")
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].strip():
            raise ValueError(f"{path}:{line_no}: expected plugin, level, and comment")
        plugin = parts[0].strip()
        level = parts[1].strip().upper()
        if not plugin.isdigit() or level not in _LEVELS:
            raise ValueError(f"{path}:{line_no}: unsupported plugin or level")
        if plugin in rules:
            raise ValueError(f"{path}:{line_no}: duplicate plugin {plugin}")
        rules[plugin] = level
        previous = ""
    return rules


def _risk_code(alert: dict) -> int:
    raw = alert.get("riskcode", alert.get("riskCode"))
    if raw is not None and str(raw).strip() != "":
        try:
            return int(str(raw).strip())
        except ValueError:
            pass
    label = str(alert.get("riskdesc") or alert.get("risk") or "").strip().lower()
    for code, name in ((3, "high"), (2, "medium"), (1, "low"), (0, "informational")):
        if label.startswith(name):
            return code
    raise ValueError(
        "Alert "
        + str(alert.get("pluginid") or alert.get("pluginId") or "unknown")
        + " has no risk rating"
    )


def _plugin_id(alert: dict) -> str:
    raw = str(alert.get("pluginid") or alert.get("pluginId") or "").strip()
    return raw.split("-", 1)[0]


def _location(url: str) -> str:
    path = urlsplit(url).path or "/"
    return path if path.startswith("/") else "/"


def _sites(report: object, target_host: str) -> list[dict]:
    if not isinstance(report, dict):
        raise ValueError("ZAP report must be a JSON object")
    sites = report.get("site")
    if sites is None:
        raise ValueError("ZAP report has no site list")
    if not isinstance(sites, list) or not sites:
        raise ValueError("ZAP report did not include the production site")
    checked: list[dict] = []
    for site in sites:
        if not isinstance(site, dict):
            raise ValueError("ZAP report site entry is not an object")
        site_host = str(site.get("@host") or "").lower().rstrip(".")
        if site_host != target_host:
            shown = site_host or "an unnamed host"
            raise ValueError(f"ZAP report included {shown}; the scan is limited to {target_host}")
        checked.append(site)
    return checked


def classify_alerts(
    report: object, *, target: str, rules: dict[str, str]
) -> tuple[list[dict], dict[str, int]]:
    """Split a baseline report into remaining high alerts and risk counts.

    Counts include every alert on the production host. High alerts whose plugin
    is ``IGNORE`` are omitted from the returned list and counted as ignored.
    A report that names any other host is rejected.
    """
    origin = normalize_origin(target)
    host = urlsplit(origin).hostname or ""
    ignored = {plugin for plugin, level in rules.items() if level == "IGNORE"}
    counts = {"high": 0, "medium": 0, "low": 0, "informational": 0, "ignored": 0}
    highs: list[dict] = []
    for site in _sites(report, host):
        alerts = site.get("alerts") or []
        if not isinstance(alerts, list):
            raise ValueError("ZAP report alerts must be a list")
        for alert in alerts:
            if not isinstance(alert, dict):
                raise ValueError("ZAP report alert is not an object")
            url = str(alert.get("url") or "")
            if url:
                alert_host = (urlsplit(url).hostname or "").lower().rstrip(".")
                if alert_host and alert_host != host:
                    raise ValueError(
                        f"ZAP alert URL is on {alert_host}; the scan is limited to {host}"
                    )
            plugin = _plugin_id(alert)
            if plugin in ignored:
                counts["ignored"] += 1
                continue
            code = _risk_code(alert)
            if code >= 3:
                counts["high"] += 1
                highs.append(alert)
            elif code == 2:
                counts["medium"] += 1
            elif code == 1:
                counts["low"] += 1
            else:
                counts["informational"] += 1
    return highs, counts


def summary_text(target: str, highs: list[dict], counts: dict[str, int]) -> str:
    """Short job summary. Paths only, so query strings stay out of the log."""
    lines = [
        "## ZAP baseline",
        "",
        f"Target: `{target}`",
        "",
        "| Risk | Alerts |",
        "| --- | ---: |",
        f"| High | {counts['high']} |",
        f"| Medium | {counts['medium']} |",
        f"| Low | {counts['low']} |",
        f"| Informational | {counts['informational']} |",
        f"| Ignored by rules file | {counts['ignored']} |",
        "",
    ]
    if not highs:
        lines.append("No high-severity alerts outside the rules file.")
    else:
        lines.append("High-severity alerts:")
        for alert in highs[:20]:
            name = str(alert.get("name") or alert.get("alert") or "alert").replace("\n", " ")
            lines.append(
                f"- `{_plugin_id(alert)}` {name} `{_location(str(alert.get('url') or ''))}`"
            )
        if len(highs) > 20:
            lines.append(f"- {len(highs) - 20} more high-severity alerts")
    return "\n".join(lines) + "\n"


def _write_step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("origin", help="print the public production origin")
    gate = sub.add_parser("gate", help="fail when the report has a high alert")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--rules", type=Path, required=True)
    gate.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "origin":
            print(production_origin())
            return 0
        rules = load_rules(args.rules)
        report = json.loads(args.report.read_text(encoding="utf-8"))
        highs, counts = classify_alerts(report, target=args.target, rules=rules)
        text = summary_text(normalize_origin(args.target), highs, counts)
    except (TargetRejected, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(text, end="")
    _write_step_summary(text)
    return 1 if highs else 0


if __name__ == "__main__":
    sys.exit(main())
