#!/usr/bin/env python3
"""Sync a PII-FREE projection of the THO ``leads`` Firestore collection into
BigQuery for analytics + dashboards (Looker Studio).

Privacy by design: this NEVER copies names, phones, or emails into BigQuery. It
loads only attribution (source / UTM / referrer), status, timing, and boolean
``has_contact`` flags — everything you need for lead/marketing analytics, nothing
that identifies a customer. The raw PII stays only in Firestore.

Creates (idempotently) dataset ``tho_analytics``, table ``leads``, and a set of
reporting views. Each run does a full ``WRITE_TRUNCATE`` reload (the leads table
is small), so it's safe to re-run and safe to schedule.

Usage:
    python scripts/sync_leads_to_bigquery.py                 # sync
    python scripts/sync_leads_to_bigquery.py --dry-run       # preview, no writes
    python scripts/sync_leads_to_bigquery.py --project tho-ai-agent --dataset tho_analytics
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

try:
    from google.cloud import bigquery, firestore
except ImportError:  # projection helpers remain unit-testable without cloud extras
    firestore = None

    class _SchemaField:
        def __init__(self, name, field_type):
            self.name = name
            self.field_type = field_type

    class _BigQuerySchemaStub:
        SchemaField = _SchemaField

    bigquery = _BigQuerySchemaStub()

# Fields that must NEVER reach BigQuery. Enforced by the projection (which only
# emits has_* booleans) AND an explicit guard before load.
_PII_FIELDS = {
    "name",
    "phone",
    "email",
    "message",
    "notes",
    "client_ip",
    "journey_id",
    "session_id",
    "user_id",
}
_JOURNEY_ID_RE = re.compile(r"^j_[0-9a-f]{32}$")
_EVENT_ALIASES = {"tour_click": "tour_opened"}
_EVENT_ALLOWLIST = {
    "page_viewed",
    "home_view",
    "home_viewed",
    "lead_form_opened",
    "tour_opened",
    "photo_clicked",
    "inventory_show_more",
    "appointment_handoff_started",
    "appointment_handoff_completed",
    "phone_clicked",
    "lead_captured",
    "appointment_booked",
    "review_redirect",
}


def _iso(value):
    """Return an ISO-8601 string for a Firestore timestamp (datetime or str)."""
    if not value:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return None


def _journey_key(value) -> str | None:
    """One-way join key for high-entropy, tab-scoped journey IDs."""
    candidate = str(value or "").strip().lower()
    if not _JOURNEY_ID_RE.fullmatch(candidate):
        return None
    return hashlib.sha256(candidate.encode()).hexdigest()


def project_lead(doc: dict) -> dict:
    """PII-free projection of one lead document (no name/phone/email values)."""
    return {
        "lead_id": doc.get("lead_id"),
        "journey_key": _journey_key(doc.get("journey_id")),
        "home_id": doc.get("home_id"),
        "home_model": doc.get("home_model"),
        "source": doc.get("source"),
        "status": doc.get("status"),
        "priority": doc.get("priority"),
        "assigned_to": doc.get("assigned_to"),
        "triage_reason": doc.get("triage_reason"),
        "contact_consent_at": _iso(doc.get("contact_consent_at")),
        "contact_consent_source": doc.get("contact_consent_source"),
        "callback_requested": doc.get("triage_reason") == "callback_requested",
        "utm_source": doc.get("utm_source"),
        "utm_medium": doc.get("utm_medium"),
        "utm_campaign": doc.get("utm_campaign"),
        "utm_content": doc.get("utm_content"),
        "utm_term": doc.get("utm_term"),
        "referrer": doc.get("referrer"),
        "has_name": bool(doc.get("name")),
        "has_phone": bool(doc.get("phone")),
        "has_email": bool(doc.get("email")),
        "homes_viewed_count": len(doc.get("homes_viewed") or []),
        "appointment_requested": bool(doc.get("appointment_requested")),
        "financing_discussed": bool(doc.get("financing_discussed")),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }


def project_event(doc: dict) -> dict | None:
    """Strict, PII-free projection of one allowlisted analytics event."""
    event = _EVENT_ALIASES.get(str(doc.get("event") or "").strip(), doc.get("event"))
    if event not in _EVENT_ALLOWLIST:
        return None
    props = doc.get("props") if isinstance(doc.get("props"), dict) else {}
    return {
        "event": event,
        "schema_version": int(doc.get("schema_version") or 1),
        "journey_key": _journey_key(doc.get("journey_id")),
        "home_id": props.get("home_id"),
        "home_model": props.get("home"),
        "source": props.get("source"),
        "intent": props.get("intent") or props.get("type"),
        "placement": props.get("placement"),
        "page_path": props.get("page_path") or props.get("path"),
        "created_at": _iso(doc.get("created_at")),
    }


_APPT_DATE_FIELDS = (
    "created_at",
    "scheduled_date",
    "appointment_date",
    "date",
    "start_time",
    "slot",
)


def _day(value):
    """Return an ISO date (YYYY-MM-DD) for a Firestore timestamp (datetime or str)."""
    if not value:
        return None
    if isinstance(value, str):
        return value[:10] or None
    try:
        return value.date().isoformat()
    except Exception:
        try:
            return value.isoformat()[:10]
        except Exception:
            return None


def build_daily_metrics(fs, lead_rows):
    """PII-free daily funnel counts: chat traffic -> leads -> reachable -> appointments.
    Reads only creation timestamps; never a name/phone/email value."""
    days = defaultdict(
        lambda: {"chat_sessions": 0, "leads": 0, "contact_leads": 0, "appointments": 0}
    )
    for doc in fs.collection("chat_sessions").stream():
        d = _day(doc.to_dict().get("created_at"))
        if d:
            days[d]["chat_sessions"] += 1
    for r in lead_rows:  # reuse the already-extracted PII-free lead rows
        d = _day(r.get("created_at"))
        if d:
            days[d]["leads"] += 1
            if r.get("has_phone") or r.get("has_email"):
                days[d]["contact_leads"] += 1
    for doc in fs.collection("appointments").stream():
        data = doc.to_dict()
        d = None
        for field in _APPT_DATE_FIELDS:
            d = _day(data.get(field))
            if d:
                break
        if d:
            days[d]["appointments"] += 1
    return [{"day": day, **vals} for day, vals in sorted(days.items())]


def _as_utc(value):
    """Parse an ISO-8601 lead timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def check_lead_freshness(rows, window_days=3):
    """PII-free 'are leads still coming in?' check computed from the already-loaded
    rows (no extra Firestore read). ``alert`` is True when zero leads landed in the
    window — the early warning for a demand or plumbing problem."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    cutoff7 = now - timedelta(days=7)
    times = [t for t in (_as_utc(r.get("created_at")) for r in rows) if t]
    last = max(times) if times else None
    return {
        "window_days": window_days,
        "leads_recent": sum(1 for t in times if t >= cutoff),
        "leads_7d": sum(1 for t in times if t >= cutoff7),
        "last_lead_at": last.isoformat() if last else None,
        "days_since_last_lead": (now - last).days if last else None,
        "alert": sum(1 for t in times if t >= cutoff) == 0,
    }


def _post_alert(webhook, payload):
    """Best-effort POST of the freshness alert to an opt-in webhook. Never raises;
    only ever called when the operator has set THO_LEAD_ALERT_WEBHOOK themselves."""
    import urllib.request

    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8).read()
        print(f"lead alert posted to webhook ({webhook.split('//')[-1][:24]}…)")
    except Exception as exc:  # noqa: BLE001 — alerting must never break the sync
        print(f"warn: lead-alert webhook failed ({exc}); banner still emitted.", file=sys.stderr)


def report_freshness(fresh, allow_webhook=True):
    """Emit the freshness result: a loud stderr banner when zero recent leads (so a
    launchd/Cloud Run log makes it obvious), plus an opt-in webhook notify."""
    if fresh["alert"]:
        banner = (
            f"⚠️  THO LEAD ALERT: 0 leads in the last {fresh['window_days']} days "
            f"(last lead {fresh['days_since_last_lead']}d ago; {fresh['leads_7d']} in the last 7d). "
            f"Check demand/plumbing."
        )
        print(banner, file=sys.stderr)
        webhook = os.environ.get("THO_LEAD_ALERT_WEBHOOK")
        if webhook and allow_webhook:
            _post_alert(webhook, {"text": banner, **fresh})
    else:
        print(
            f"lead freshness OK: {fresh['leads_recent']} lead(s) in last {fresh['window_days']}d, "
            f"{fresh['leads_7d']} in 7d (last {fresh['days_since_last_lead']}d ago)."
        )


SCHEMA = [
    bigquery.SchemaField("lead_id", "STRING"),
    bigquery.SchemaField("journey_key", "STRING"),
    bigquery.SchemaField("home_id", "STRING"),
    bigquery.SchemaField("home_model", "STRING"),
    bigquery.SchemaField("source", "STRING"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("priority", "STRING"),
    bigquery.SchemaField("assigned_to", "STRING"),
    bigquery.SchemaField("triage_reason", "STRING"),
    bigquery.SchemaField("contact_consent_at", "TIMESTAMP"),
    bigquery.SchemaField("contact_consent_source", "STRING"),
    bigquery.SchemaField("callback_requested", "BOOL"),
    bigquery.SchemaField("utm_source", "STRING"),
    bigquery.SchemaField("utm_medium", "STRING"),
    bigquery.SchemaField("utm_campaign", "STRING"),
    bigquery.SchemaField("utm_content", "STRING"),
    bigquery.SchemaField("utm_term", "STRING"),
    bigquery.SchemaField("referrer", "STRING"),
    bigquery.SchemaField("has_name", "BOOL"),
    bigquery.SchemaField("has_phone", "BOOL"),
    bigquery.SchemaField("has_email", "BOOL"),
    bigquery.SchemaField("homes_viewed_count", "INTEGER"),
    bigquery.SchemaField("appointment_requested", "BOOL"),
    bigquery.SchemaField("financing_discussed", "BOOL"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
    bigquery.SchemaField("updated_at", "TIMESTAMP"),
]

EVENT_SCHEMA = [
    bigquery.SchemaField("event", "STRING"),
    bigquery.SchemaField("schema_version", "INTEGER"),
    bigquery.SchemaField("journey_key", "STRING"),
    bigquery.SchemaField("home_id", "STRING"),
    bigquery.SchemaField("home_model", "STRING"),
    bigquery.SchemaField("source", "STRING"),
    bigquery.SchemaField("intent", "STRING"),
    bigquery.SchemaField("placement", "STRING"),
    bigquery.SchemaField("page_path", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
]

DAILY_SCHEMA = [
    bigquery.SchemaField("day", "DATE"),
    bigquery.SchemaField("chat_sessions", "INTEGER"),
    bigquery.SchemaField("leads", "INTEGER"),
    bigquery.SchemaField("contact_leads", "INTEGER"),
    bigquery.SchemaField("appointments", "INTEGER"),
]

# Reporting views. `{d}` = fully-qualified dataset (project.dataset).
VIEWS = {
    "v_lead_summary": (
        "SELECT COUNT(*) total_leads, "
        "COUNTIF(has_phone OR has_email) contact_leads, "
        "COUNTIF(utm_source IS NOT NULL) attributed_leads, "
        "COUNTIF(DATE(created_at)=CURRENT_DATE()) leads_today, "
        "COUNTIF(created_at>=TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 7 DAY)) leads_7d, "
        "COUNTIF(created_at>=TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 30 DAY)) leads_30d "
        "FROM `{d}.leads`"
    ),
    "v_leads_by_source": (
        "SELECT COALESCE(source,'unknown') source, COUNT(*) leads, "
        "COUNTIF(has_phone OR has_email) contact_leads "
        "FROM `{d}.leads` GROUP BY 1 ORDER BY leads DESC"
    ),
    "v_leads_by_campaign": (
        "SELECT COALESCE(utm_source,'(direct/none)') utm_source, "
        "COALESCE(utm_medium,'(none)') utm_medium, "
        "COALESCE(utm_campaign,'(none)') utm_campaign, "
        "COUNT(*) leads, COUNTIF(has_phone OR has_email) contact_leads "
        "FROM `{d}.leads` GROUP BY 1,2,3 ORDER BY leads DESC"
    ),
    "v_daily_leads": (
        "SELECT DATE(created_at) day, COALESCE(source,'unknown') source, "
        "COUNT(*) leads, COUNTIF(has_phone OR has_email) contact_leads "
        "FROM `{d}.leads` WHERE created_at IS NOT NULL GROUP BY 1,2 ORDER BY day DESC"
    ),
    "v_lead_freshness": (
        "SELECT MAX(created_at) last_lead_at, "
        "DATE_DIFF(CURRENT_DATE(), DATE(MAX(created_at)), DAY) days_since_last_lead, "
        "COUNTIF(created_at>=TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 3 DAY)) leads_3d, "
        "COUNTIF(created_at>=TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 7 DAY)) leads_7d "
        "FROM `{d}.leads`"
    ),
    "v_journey_funnel": (
        "SELECT journey_key, MIN(created_at) first_event_at, MAX(created_at) last_event_at, "
        "COUNT(*) events, COUNTIF(event='lead_form_opened')>0 opened_lead_form, "
        "COUNTIF(event='phone_clicked')>0 clicked_phone, "
        "COUNTIF(event='lead_captured')>0 became_lead, "
        "COUNTIF(event='appointment_booked')>0 booked_appointment "
        "FROM `{d}.events` WHERE journey_key IS NOT NULL GROUP BY journey_key"
    ),
    "v_home_conversion": (
        "SELECT COALESCE(home_id,'(unknown)') home_id, "
        "ANY_VALUE(home_model HAVING MAX created_at) home_model, "
        "COUNT(DISTINCT journey_key) engaged_journeys, "
        "COUNT(DISTINCT IF(event='lead_captured',journey_key,NULL)) lead_journeys, "
        "COUNT(DISTINCT IF(event='appointment_booked',journey_key,NULL)) appointment_journeys, "
        "ROUND(100*SAFE_DIVIDE(COUNT(DISTINCT IF(event='lead_captured',journey_key,NULL)),"
        "NULLIF(COUNT(DISTINCT journey_key),0)),1) lead_conversion_pct, "
        "ROUND(100*SAFE_DIVIDE(COUNT(DISTINCT IF(event='appointment_booked',journey_key,NULL)),"
        "NULLIF(COUNT(DISTINCT journey_key),0)),1) appointment_conversion_pct "
        "FROM `{d}.events` WHERE journey_key IS NOT NULL AND home_id IS NOT NULL "
        "GROUP BY 1 ORDER BY lead_journeys DESC, engaged_journeys DESC"
    ),
    "v_conversion_by_source": (
        "SELECT COALESCE(source,'(direct/unknown)') source, "
        "COUNT(DISTINCT IF(event='lead_captured',journey_key,NULL)) lead_journeys, "
        "COUNT(DISTINCT IF(event='appointment_booked',journey_key,NULL)) appointment_journeys "
        "FROM `{d}.events` WHERE journey_key IS NOT NULL GROUP BY 1 "
        "ORDER BY lead_journeys DESC, appointment_journeys DESC"
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="tho-ai-agent")
    ap.add_argument("--dataset", default="tho_analytics")
    ap.add_argument("--location", default="US")
    ap.add_argument("--dry-run", action="store_true", help="Extract + project, but write nothing.")
    args = ap.parse_args()

    if firestore is None or not hasattr(bigquery, "Client"):
        print(
            "google-cloud-bigquery and google-cloud-firestore are required to run the sync.",
            file=sys.stderr,
        )
        return 2

    fs = firestore.Client(project=args.project)
    rows = [project_lead(d.to_dict()) for d in fs.collection("leads").stream()]
    event_rows = [
        projected
        for doc in fs.collection("analytics_events").stream()
        if (projected := project_event(doc.to_dict())) is not None
    ]

    # Hard PII guard: fail loudly if any projected row somehow carries a raw
    # name/phone/email value (it shouldn't — the projection only emits has_*).
    for r in rows:
        leaked = _PII_FIELDS & set(r)
        if leaked:
            print(
                f"ABORT: PII field(s) {leaked} in projected row — refusing to load.",
                file=sys.stderr,
            )
            return 2
    for event_row in event_rows:
        leaked = _PII_FIELDS & set(event_row)
        if leaked:
            print(
                f"ABORT: PII field(s) {leaked} in event row — refusing to load.",
                file=sys.stderr,
            )
            return 2

    print(f"extracted {len(rows)} PII-free lead rows from firestore://{args.project}/leads")
    print(
        f"extracted {len(event_rows)} PII-free event rows from "
        f"firestore://{args.project}/analytics_events"
    )
    report_freshness(check_lead_freshness(rows), allow_webhook=not args.dry_run)
    if args.dry_run:
        for r in rows[:3]:
            print("  sample:", json.dumps(r))
        for event_row in event_rows[:3]:
            print("  event sample:", json.dumps(event_row))
        print("dry-run: no BigQuery writes.")
        return 0

    bq = bigquery.Client(project=args.project)
    dsref = f"{args.project}.{args.dataset}"

    ds = bigquery.Dataset(dsref)
    ds.location = args.location
    ds.description = "THO lead analytics — PII-FREE projection of the Firestore leads collection (no names/phones/emails). Rebuilt by scripts/sync_leads_to_bigquery.py."
    bq.create_dataset(ds, exists_ok=True)

    table_id = f"{dsref}.leads"
    job = bq.load_table_from_json(
        rows,
        table_id,
        job_config=bigquery.LoadJobConfig(schema=SCHEMA, write_disposition="WRITE_TRUNCATE"),
    )
    job.result()
    print(f"loaded {len(rows)} rows -> {table_id} (WRITE_TRUNCATE)")

    event_table_id = f"{dsref}.events"
    bq.load_table_from_json(
        event_rows,
        event_table_id,
        job_config=bigquery.LoadJobConfig(
            schema=EVENT_SCHEMA, write_disposition="WRITE_TRUNCATE"
        ),
    ).result()
    print(f"loaded {len(event_rows)} rows -> {event_table_id} (WRITE_TRUNCATE)")

    for name, sql in VIEWS.items():
        vid = f"{dsref}.{name}"
        bq.delete_table(vid, not_found_ok=True)
        view = bigquery.Table(vid)
        view.view_query = sql.format(d=dsref)
        bq.create_table(view)
        print(f"created view -> {vid}")

    # Daily funnel metrics (PII-free counts): traffic -> lead -> reachable -> appointment.
    daily = build_daily_metrics(fs, rows)
    dt_id = f"{dsref}.daily_metrics"
    bq.load_table_from_json(
        daily,
        dt_id,
        job_config=bigquery.LoadJobConfig(schema=DAILY_SCHEMA, write_disposition="WRITE_TRUNCATE"),
    ).result()
    print(f"loaded {len(daily)} daily-metric rows -> {dt_id}")

    funnel_sql = (
        "SELECT SUM(chat_sessions) chat_sessions, SUM(leads) leads, "
        "SUM(contact_leads) contact_leads, SUM(appointments) appointments, "
        "ROUND(100*SAFE_DIVIDE(SUM(leads),NULLIF(SUM(chat_sessions),0)),1) session_to_lead_pct, "
        "ROUND(100*SAFE_DIVIDE(SUM(contact_leads),NULLIF(SUM(leads),0)),1) lead_to_contact_pct, "
        "ROUND(100*SAFE_DIVIDE(SUM(appointments),NULLIF(SUM(contact_leads),0)),1) contact_to_appt_pct "
        f"FROM `{dsref}.daily_metrics` WHERE day >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"
    )
    fvid = f"{dsref}.v_funnel_30d"
    bq.delete_table(fvid, not_found_ok=True)
    fview = bigquery.Table(fvid)
    fview.view_query = funnel_sql
    bq.create_table(fview)
    print(f"created view -> {fvid}")

    print("done. Point Looker Studio at the views above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
