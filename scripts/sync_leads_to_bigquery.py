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
import sys
from collections import defaultdict

from google.cloud import bigquery, firestore

# Fields that must NEVER reach BigQuery. Enforced by the projection (which only
# emits has_* booleans) AND an explicit guard before load.
_PII_FIELDS = {"name", "phone", "email"}


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


def project_lead(doc: dict) -> dict:
    """PII-free projection of one lead document (no name/phone/email values)."""
    return {
        "lead_id": doc.get("lead_id"),
        "source": doc.get("source"),
        "status": doc.get("status"),
        "priority": doc.get("priority"),
        "assigned_to": doc.get("assigned_to"),
        "triage_reason": doc.get("triage_reason"),
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


_APPT_DATE_FIELDS = ("created_at", "scheduled_date", "appointment_date", "date", "start_time", "slot")


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
    days = defaultdict(lambda: {"chat_sessions": 0, "leads": 0, "contact_leads": 0, "appointments": 0})
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


SCHEMA = [
    bigquery.SchemaField("lead_id", "STRING"),
    bigquery.SchemaField("source", "STRING"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("priority", "STRING"),
    bigquery.SchemaField("assigned_to", "STRING"),
    bigquery.SchemaField("triage_reason", "STRING"),
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
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="tho-ai-agent")
    ap.add_argument("--dataset", default="tho_analytics")
    ap.add_argument("--location", default="US")
    ap.add_argument("--dry-run", action="store_true", help="Extract + project, but write nothing.")
    args = ap.parse_args()

    fs = firestore.Client(project=args.project)
    rows = [project_lead(d.to_dict()) for d in fs.collection("leads").stream()]

    # Hard PII guard: fail loudly if any projected row somehow carries a raw
    # name/phone/email value (it shouldn't — the projection only emits has_*).
    for r in rows:
        leaked = _PII_FIELDS & set(r)
        if leaked:
            print(f"ABORT: PII field(s) {leaked} in projected row — refusing to load.", file=sys.stderr)
            return 2

    print(f"extracted {len(rows)} PII-free lead rows from firestore://{args.project}/leads")
    if args.dry_run:
        import json
        for r in rows[:3]:
            print("  sample:", json.dumps(r))
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
