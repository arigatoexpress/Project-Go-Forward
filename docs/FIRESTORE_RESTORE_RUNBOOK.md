# Firestore Backup & Restore Runbook — Texas Home Outlet

> **Application recovery requires a separately reviewed method — September 10, 2026.**
> The current application constructs Firestore clients with the default database
> and does not read `FIRESTORE_DATABASE`. Setting that environment variable will
> create a revision but will not redirect its database clients. A restore into an
> isolated database can be validated separately; do not claim the application has
> switched to it. Before any recovery cutover, implement and test database selection
> consistently across all application clients, or prepare another reviewed restore
> method. Require explicit approval for production data/traffic changes and validate
> the actual database identity. Backup existence is not a completed restore drill.

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

This is a **managed Firestore backup**, not an `gcloud firestore export`. Restores are done through the Cloud Console or `gcloud alpha firestore databases restore`.

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

gcloud alpha firestore databases restore \
  --source-backup="$BACKUP" \
  --project="$PROJECT_ID" \
  --destination-database="$NEW_DB" \
  --format="value(name)"
```

This is **non-destructive** to the live database. Validate the restored data before cutting over.

### 4.3 Cut over the app

The current application does **not** support selecting a restored database through
`FIRESTORE_DATABASE`. Setting that variable does not redirect its clients. Validate
the isolated database using section 6, then prepare a reviewed recovery method with
actual application/database identity checks and explicit production-cutover approval.

### 4.4 Rollback if the restore is bad

Prepare rollback for the chosen recovery method before cutover (`docs/RUNBOOK.md`
§2). Removing an ignored environment variable does not switch databases or undo
an import. Do not assume a traffic rollback reverses database writes.

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

An import creates documents from the export and **overwrites matching document
IDs**. Documents absent from the export remain in the target. Import is not an
atomic replacement: canceling it leaves writes already applied. Use an empty,
isolated target and verify completion before validation. See Google's
[import behavior and cancellation guidance](https://firebase.google.com/docs/firestore/manage-data/export-import#import_data).

---

## 6. Post-restore validation

### 6.1 Validate the isolated restored database

Set the project and database IDs from the completed restore/import receipt.
The example requires both explicitly and refuses the live `(default)` database.
It fetches document IDs only; do not print customer data or secrets into receipts.
These validation variables are local to this script, not application settings.

```bash
VALIDATION_PROJECT="${PROJECT_ID:?Set the restored project ID}" \
VALIDATION_DATABASE="${NEW_DB:?Set the restored database ID}" python3 - <<'PY'
import os
from google.cloud import firestore

project = os.environ["VALIDATION_PROJECT"].strip()
database = os.environ["VALIDATION_DATABASE"].strip()
if not project or not database or database == "(default)":
    raise SystemExit("Select the explicit isolated restored project/database")
db = firestore.Client(project=project, database=database)
print(f"Validation target: projects/{project}/databases/{database}")
for col in ["customers", "inventory", "deals", "service_requests", "appointments", "analytics_events"]:
    count = sum(1 for _ in db.collection(col).select([]).limit(1000).stream(timeout=10))
    print(f"{col}: {count} (capped at 1000; excludes subcollections)")
PY
```

Compare against approved expectations for that backup. Capped counts alone do not
prove complete recovery: validate required document relationships, subcollections,
indexes and encrypted-field recovery through an approved process. Application health
does not establish that the application uses this database.

### 6.2 Check the serving application separately

The following read-only probes check the serving application, which may still use
the original database. Record its serving commit separately from the restored
database's validation evidence.

```bash
curl -fsS https://www.texashomeoutlet.com/healthz/ | python3 -m json.tool
.venv/bin/python scripts/production_smoke.py --base-url https://www.texashomeoutlet.com
```

### 6.3 Approve any live end-to-end probe separately

A live contact POST creates records and may send staff/customer notifications even
with synthetic contact details. It is **not** part of the read-only validation above.
Require explicit owner approval for the exact payload and expected recipients/side
effects before executing it; use an isolated synthetic test otherwise. A successful
HTTP response is not proof that an email reached an inbox.

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
- **Assuming an environment variable switches databases.** Current clients ignore `FIRESTORE_DATABASE`; verify the chosen recovery method's actual database identity.
- **Importing into a non-empty database.** Matching documents are overwritten and unrelated documents remain; use an empty isolated target.
- **Not testing restores.** Run a test restore quarterly to prove the process.
