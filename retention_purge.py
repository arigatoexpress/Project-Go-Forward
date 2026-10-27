"""Dry-run-by-default retention purge for personal and transient Firestore data.

Security item R13. Retention periods are proposed in ``config.yaml`` and the
purge ships disabled. This module does not schedule itself, change IAM, or
open the production project unless that is explicitly overridden.

Deletes require both ``data_retention.enabled: true`` and ``--execute``.
A disabled config does not read or delete anything. A dry-run reports counts
and a few document ids, never document bodies.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from google.cloud import firestore

logger = logging.getLogger(__name__)

PRODUCTION_PROJECT_ID = "tho-ai-agent"
ALLOW_PRODUCTION_ENV = "THO_RETENTION_PURGE_ALLOW_PRODUCTION"
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500
SAMPLE_ID_LIMIT = 5
# Operator script, not a request-path call. The serving timeout is 10s and is
# too short for a collection scan. Deletes happen only after that scan returns.
PURGE_RPC_TIMEOUT_SECONDS = 300.0

# Eligible collections and the only timestamp field this purge may age them by.
# Business records, live credentials, and the audit trail are intentionally
# absent: customers, deals, inventory, sales, leases, tax payments, service
# requests, tho_admin_credentials, tho_admin_staff, audit_log, activities.
PURGE_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("chat_sessions", "updated_at"),
    ("conversation_contexts", "updated_at"),
    ("leads", "created_at"),
    ("appointments", "created_at"),
    ("feedback", "created_at"),
    ("tho_admin_login_codes", "created_at"),
    ("google_ads_owner_step_up_nonces", "issued_at"),
    ("user_activity_log", "timestamp"),
    ("analytics_events", "created_at"),
    ("email_log", "sent_at"),
    ("email_reply_drafts", "created_at"),
    ("nurture_log", "sent_at"),
)
PURGE_ALLOWLIST: dict[str, str] = dict(PURGE_COLLECTIONS)


class RetentionConfigError(ValueError):
    """The enabled retention config cannot be trusted. Delete nothing."""


@dataclass(frozen=True)
class CollectionPolicy:
    name: str
    retention_days: int
    timestamp_field: str


@dataclass(frozen=True)
class Rejection:
    collection: str
    reason: str


@dataclass(frozen=True)
class RetentionPolicy:
    enabled: bool
    batch_size: int
    collections: tuple[CollectionPolicy, ...]
    rejections: tuple[Rejection, ...]


@dataclass
class CollectionReport:
    collection: str
    status: str
    retention_days: int | None = None
    timestamp_field: str | None = None
    scanned: int = 0
    eligible: int = 0
    skipped_malformed: int = 0
    would_delete: int = 0
    deleted: int = 0
    capped: bool = False
    sample_ids: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class PurgeReport:
    mode: str
    enabled: bool
    execute_requested: bool
    deleted: int
    batch_size: int
    detail: str
    collections: list[CollectionReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_timestamp(value: Any) -> datetime | None:
    """Return a UTC timestamp, or None when the value should not drive a delete."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _collection_label(name: Any) -> str:
    if isinstance(name, str) and 1 <= len(name) <= 120 and all(ord(ch) >= 32 for ch in name):
        return name
    return "(invalid)"


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def load_retention_policy(config: dict[str, Any]) -> RetentionPolicy:
    """Build a policy from config. Anything other than enabled=true is a no-op."""
    section = config.get("data_retention") if isinstance(config, dict) else None
    if not isinstance(section, dict) or section.get("enabled") is not True:
        return RetentionPolicy(
            enabled=False,
            batch_size=DEFAULT_BATCH_SIZE,
            collections=(),
            rejections=(),
        )

    batch_size = section.get("batch_size", DEFAULT_BATCH_SIZE)
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or not 1 <= batch_size <= MAX_BATCH_SIZE
    ):
        raise RetentionConfigError(
            "data_retention.batch_size must be an integer from 1 to 500 when purge is enabled"
        )

    raw_collections = section.get("collections")
    if not isinstance(raw_collections, dict):
        raise RetentionConfigError(
            "data_retention.collections must be a mapping when purge is enabled"
        )

    accepted: list[CollectionPolicy] = []
    rejections: list[Rejection] = []
    for name, spec in raw_collections.items():
        label = _collection_label(name)
        expected_field = PURGE_ALLOWLIST.get(name) if isinstance(name, str) else None
        if expected_field is None:
            rejections.append(Rejection(label, "collection is not eligible for this purge"))
            continue
        if not isinstance(spec, dict):
            rejections.append(Rejection(label, "collection settings must be a mapping"))
            continue
        if spec.get("timestamp_field") != expected_field:
            rejections.append(
                Rejection(label, "timestamp_field does not match the purge allowlist")
            )
            continue
        if not _positive_int(spec.get("retention_days")):
            rejections.append(Rejection(label, "retention_days must be a positive integer"))
            continue
        accepted.append(
            CollectionPolicy(
                name=name,
                retention_days=int(spec["retention_days"]),
                timestamp_field=expected_field,
            )
        )

    return RetentionPolicy(
        enabled=True,
        batch_size=batch_size,
        collections=tuple(accepted),
        rejections=tuple(rejections),
    )


def read_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).resolve().parent / "config.yaml"
    with config_path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise RetentionConfigError("config.yaml must be a mapping")
    return loaded


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    parsed = parse_timestamp(now)
    if parsed is None:
        raise ValueError("purge clock must be a real timestamp")
    return parsed


def _select_expired(
    client: Any,
    policy: CollectionPolicy,
    *,
    now: datetime,
    batch_size: int,
    execute: bool,
) -> tuple[CollectionReport, list[str]]:
    cutoff = now - timedelta(days=policy.retention_days)
    scanned = 0
    skipped = 0
    eligible: list[tuple[datetime, str]] = []
    collection = client.collection(policy.name)
    for doc in collection.stream(timeout=PURGE_RPC_TIMEOUT_SECONDS):
        scanned += 1
        doc_id = getattr(doc, "id", None)
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not isinstance(doc_id, str) or not doc_id or not isinstance(data, dict):
            skipped += 1
            continue
        stamp = parse_timestamp(data.get(policy.timestamp_field))
        if stamp is None:
            skipped += 1
            continue
        if stamp < cutoff:
            eligible.append((stamp, doc_id))

    eligible.sort(key=lambda item: (item[0], item[1]))
    selected_ids = [doc_id for _, doc_id in eligible[:batch_size]]
    report = CollectionReport(
        collection=policy.name,
        status="execute" if execute else "dry_run",
        retention_days=policy.retention_days,
        timestamp_field=policy.timestamp_field,
        scanned=scanned,
        eligible=len(eligible),
        skipped_malformed=skipped,
        would_delete=len(selected_ids),
        deleted=0,
        capped=len(eligible) > batch_size,
        sample_ids=selected_ids[:SAMPLE_ID_LIMIT],
    )
    return report, selected_ids


def _delete_ids(client: Any, collection_name: str, doc_ids: list[str]) -> int:
    if collection_name not in PURGE_ALLOWLIST:
        raise RetentionConfigError(f"refusing to delete from {collection_name}")
    if not doc_ids:
        return 0
    collection = client.collection(collection_name)
    batch = client.batch()
    for doc_id in doc_ids:
        batch.delete(collection.document(doc_id))
    batch.commit(timeout=PURGE_RPC_TIMEOUT_SECONDS)
    return len(doc_ids)


def run_purge(
    client: Any,
    policy: RetentionPolicy,
    *,
    execute: bool,
    now: datetime | None = None,
) -> PurgeReport:
    """Plan a capped purge. Delete only when the policy is enabled and execute is set.

    Every configured collection is scanned before any delete, so a read failure
    leaves the database unchanged. Each collection then deletes at most one
    batch, oldest expired documents first.
    """
    if not policy.enabled:
        return PurgeReport(
            mode="disabled",
            enabled=False,
            execute_requested=execute,
            deleted=0,
            batch_size=policy.batch_size,
            detail="Retention purge is disabled in config. Nothing was read or deleted.",
        )

    moment = _coerce_now(now)
    reports: list[CollectionReport] = []
    planned: list[tuple[str, CollectionReport, list[str]]] = []
    configured = {item.name: item for item in policy.collections}
    rejected_names = {rejection.collection for rejection in policy.rejections}
    for name, timestamp_field in PURGE_COLLECTIONS:
        if name in rejected_names:
            continue
        item = configured.get(name)
        if item is None:
            reports.append(
                CollectionReport(
                    collection=name,
                    status="not_configured",
                    timestamp_field=timestamp_field,
                    detail="No retention period is configured for this collection.",
                )
            )
            continue
        report, selected_ids = _select_expired(
            client,
            item,
            now=moment,
            batch_size=policy.batch_size,
            execute=execute,
        )
        reports.append(report)
        planned.append((item.name, report, selected_ids))

    for rejection in policy.rejections:
        logger.warning(
            "retention purge skipped ineligible collection=%s reason=%s",
            rejection.collection,
            rejection.reason,
        )
        reports.append(
            CollectionReport(
                collection=rejection.collection,
                status="rejected",
                detail=rejection.reason,
            )
        )

    deleted_total = 0
    if execute:
        for collection_name, report, selected_ids in planned:
            if not selected_ids:
                continue
            report.deleted = _delete_ids(client, collection_name, selected_ids)
            deleted_total += report.deleted

    if execute:
        detail = "Execute finished. Each collection deleted at most one capped batch."
    else:
        detail = "Dry-run: no documents were deleted."
    return PurgeReport(
        mode="execute" if execute else "dry_run",
        enabled=True,
        execute_requested=execute,
        deleted=deleted_total,
        batch_size=policy.batch_size,
        detail=detail,
        collections=reports,
    )


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def _default_client(project: str) -> Any:
    return firestore.Client(project=project)


def _refuse(execute_requested: bool, detail: str) -> int:
    _emit(
        {
            "mode": "refused",
            "enabled": True,
            "execute_requested": execute_requested,
            "deleted": 0,
            "batch_size": None,
            "detail": detail,
            "collections": [],
        }
    )
    return 2


def main(
    argv: list[str] | None = None,
    *,
    config: dict[str, Any] | None = None,
    client_factory: Any = None,
) -> int:
    """CLI entry. Dry-run by default. Disabled config never opens Firestore."""
    parser = argparse.ArgumentParser(
        description=(
            "Report or delete personal and transient Firestore documents older than "
            "the configured retention periods. Dry-run unless --execute is set. "
            "Deletes also require data_retention.enabled."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete the capped batch. Ignored while data_retention.enabled is false.",
    )
    args = parser.parse_args(argv)

    try:
        raw = config if config is not None else read_config()
        policy = load_retention_policy(raw)
    except RetentionConfigError as exc:
        _emit(
            {
                "mode": "error",
                "enabled": True,
                "execute_requested": bool(args.execute),
                "deleted": 0,
                "batch_size": None,
                "detail": str(exc),
                "collections": [],
            }
        )
        return 1

    client = None
    if policy.enabled:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project:
            return _refuse(
                bool(args.execute),
                "GOOGLE_CLOUD_PROJECT is not set. Refusing to choose a Firestore project.",
            )
        allowed = os.environ.get(ALLOW_PRODUCTION_ENV, "").strip() == "1"
        if project == PRODUCTION_PROJECT_ID and not allowed:
            return _refuse(
                bool(args.execute),
                "Refusing to open production project tho-ai-agent. "
                "Set THO_RETENTION_PURGE_ALLOW_PRODUCTION=1 only after the owner "
                "confirms the periods and a backup.",
            )
        factory = client_factory or _default_client
        client = factory(project)

    report = run_purge(client, policy, execute=bool(args.execute))
    _emit(report.to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
