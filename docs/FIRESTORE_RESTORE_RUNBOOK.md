# Firestore Backup & Restore Runbook — Texas Home Outlet

**Scope:** Google Cloud Firestore (Native mode) database `(default)` in project `tho-ai-agent`.  
**Companion docs:** `docs/RUNBOOK.md`, `docs/ON_CALL.md`, `docs/SLO.md`, `docs/READ_TIMEOUTS.md`.

This runbook covers:
1. Verifying the daily backup schedule.
2. Restoring a collection or the whole database from a managed backup.
3. Emergency point-in-time recovery via import/export.
4. Post-restore validation.

**Prerequisites:** `gcloud` CLI, `roles/datastore.owner` or `roles/owner` on `tho-ai-agent`.

---

## 1. Backup strategy today

The **Ops bootstrap workflow** (`.github/workflows/ops-bootstrap.yml`) creates a managed backup schedule:

- **Frequency:** daily
- **Retention:** 7 days
- **Database:** `(default)`

This is a **managed Firestore backup**, not an `gcloud firestore export`. Restores are done through the Cloud Console or `gcloud alpha firestore backups restore`.

---

## 2. Verify the backup schedule

```bash
export PROJECT_ID=tho-ai-agent

gcloud firestore backups schedules list --database="(default)" --project="$PROJECT_ID"
```

You should see one schedule with `recurrence: DAILY` and a retention of `604800s` (7 days).

List existing backups:

```bash
gcloud firestore backups list --project="$PROJECT_ID" --format="table(name, state, snapshotTime, expireTime)"
```

If the schedule is missing, re-run the Ops bootstrap workflow from GitHub Actions.

---

## 3. When to restore

| Scenario | Recommended action |
|---|---|
| Accidental deletion of a small number of documents | Restore from the newest managed backup **or** re-create manually if the change is small |
| Corrupted collection (e.g., bad migration) | Restore the affected collection from a backup taken before the corruption |
| Full database corruption / ransomware | Restore the entire database to a known-good backup |
| Need a copy for staging/debugging | Restore a backup into a **different** Firestore database or project |

**Important:** Firestore managed restores create a **new database** by default (to avoid overwriting the live one). Plan traffic cutover carefully.

---

## 4. Managed backup restore (whole database)

### 4.1 Find the backup to restore from

```bash
BACKUP=$(gcloud firestore backups list --project="$PROJECT_ID" \
  --format='value(name)' --filter='state=READY' | head -1)
echo "$BACKUP"
```

Pick a backup whose `snapshotTime` is before the incident.

### 4.2 Restore to a new database

```bash
NEW_DB=tho-restore-$(date -u +%Y%m%d%H%M%S)

gcloud alpha firestore backups restore "$BACKUP" \
  --project="$PROJECT_ID" \
  --destination-database="$NEW_DB" \
  --format="value(name)"
```

This is **non-destructive** to the live database. Validate the restored data before cutting over.

### 4.3 Cut over the app

Only after validation, point the Cloud Run service at the new database by setting `FIRESTORE_DATABASE`:

```bash
gcloud run services update project-go-forward \
  --project="$PROJECT_ID" \
  --region=us-central1 \
  --set-env-vars="FIRESTORE_DATABASE=$NEW_DB"
```

> **Warning:** This deploys a new revision. Have your rollback command ready (`docs/RUNBOOK.md` §2).

### 4.4 Rollback if the restore is bad

```bash
# Revert to the original (default) database
gcloud run services update project-go-forward \
  --project="$PROJECT_ID" \
  --region=us-central1 \
  --remove-env-vars="FIRESTORE_DATABASE"
```

---

## 5. Import/export restore (collection-level or cross-project)

If you need a collection-level restore or a copy in a different project, use Firestore export/import to Cloud Storage.

### 5.1 Export the live database (before risky operations, or for cross-project copy)

```bash
BUCKET="gs://${PROJECT_ID}-firestore-exports"
gsutil mb -p "$PROJECT_ID" "$BUCKET" 2>/dev/null || true

gcloud firestore export "$BUCKET/tho-$(date -u +%Y%m%d-%H%M%S)" \
  --project="$PROJECT_ID" \
  --database="(default)"
```

### 5.2 Import to a different project or database

```bash
# Example: import into the same project, database 'restore-test'
IMPORT_PATH="gs://${PROJECT_ID}-firestore-exports/tho-YYYYMMDD-HHMMSS"

gcloud firestore import "$IMPORT_PATH" \
  --project="$PROJECT_ID" \
  --database="restore-test"
```

Firestore import is **all-or-nothing for the specified export**. It does not merge with existing data; import into an empty database.

---

## 6. Post-restore validation

After any restore or cutover:

```bash
# 1. App health
curl -fsS https://www.texashomeoutlet.com/healthz/ | python3 -m json.tool

# 2. Document counts for key collections (run from a Python shell with google-cloud-firestore)
python3 - <<'PY'
from google.cloud import firestore
db = firestore.Client(project="tho-ai-agent", database="(default)")
for col in ["customers", "inventory", "deals", "service_requests", "appointments", "analytics_events"]:
    docs = list(db.collection(col).limit(1000).stream())
    print(f"{col}: {len(docs)} (sampled up to 1000)")
PY

# 3. Production smoke
.venv/bin/python scripts/production_smoke.py --base-url https://www.texashomeoutlet.com

# 4. Lead-capture end-to-end (do not submit real PII; use a test email)
curl -fsS -X POST https://www.texashomeoutlet.com/api/contact \
  -H "Content-Type: application/json" \
  -d '{"name":"Restore Test","email":"restore-test@example.invalid","message":"SLO restore validation."}'
```

---

## 7. RTO / RPO targets

| Metric | Target | Notes |
|---|---|---|
| **RPO** (max data loss) | 24 hours | Daily managed backups |
| **RTO** (time to restore service) | 1 hour | Restore to new DB + cutover + validation |

If these targets are too loose for the business, increase backup frequency or add hourly export jobs.

---

## 8. Common mistakes

- **Restoring over the live database without a validation step.** Always restore to a new DB first.
- **Forgetting to update the Cloud Run env var.** The app must point at the restored database.
- **Importing into a non-empty database.** Firestore import replaces data; use an empty target database.
- **Not testing restores.** Run a test restore quarterly to prove the process.
