# Local-Only Data Handling

Some workflows in this repo (FCD migration, inventory media enrichment,
cutover audits) consume or produce raw business data containing customer PII.
That data must never enter the repository, chat transcripts, or logs. This
document describes where it lives and how to reference it **without absolute,
machine-specific paths**.

## Rules

1. **Never commit raw source data.** Only sanitized artifacts (e.g.
   `data/migrated_customers.json`) are committed, and only after passing
   through the sanitizer with PII stripped.
2. **Never paste or log rows** from raw exports — not in commits, PRs, issues,
   chat sessions, or application logs. Use `tools/pii_guard.py` for anything
   that must be logged.
3. **No absolute paths in code or docs.** Scripts that read or write local
   data take a `--output-dir`/`--input` argument and/or honor the
   `THO_LOCAL_DATA_DIR` environment variable. Docs refer to bundles by name,
   not by path.

## Locations

| Dataset | How to locate it |
|---|---|
| FCD differential bundles | Kept outside the repo on the operator's machine. The symlink/folder `fcd_differential_latest` inside the operator's secure handoff directory points at the latest timestamped bundle. Ask the operator (Ari) for the current location; do not guess. |
| Canonical migration CSV (`full_migration_export.csv`) | Local-only raw source in the operator's business records directory. Same rule: ask, don't guess; never commit, paste, or log rows. |
| Enrichment/cutover artifacts | Default to `$THO_LOCAL_DATA_DIR`, falling back to `data/local_private/` (gitignored). |

## Environment variable

```bash
# Optional: point local-data-producing scripts at a directory outside the repo
export THO_LOCAL_DATA_DIR="$HOME/path/to/secure/dir"
```

If unset, scripts default to `data/local_private/`, which is gitignored. Keep
that directory out of backups/syncs that aren't encrypted.

## Before any Firestore write from migration data

Use `legacy_id` / `fcd_app_id` for idempotent matching, preserve existing
customer IDs, and require an explicit dry-run plus human approval — see the
FCD Migration Handoff section in `CLAUDE.md`.
