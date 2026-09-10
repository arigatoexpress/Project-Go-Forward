# Prepare and execute an approved admin PIN rotation

Audience: the named production operator for project `tho-ai-agent`. Canonical
staff site: `https://www.texashomeoutlet.com`.

The offline preparation below does not change a credential or session. Writing a
Secret Manager version, deploying a revision, changing traffic, rotating the
session secret, and notifying staff require approval for their concrete effects.
Keep working staff passkeys and a tested recovery path throughout the change.

## 1. Choose the rotation scope

`ADMIN_PIN_HASH` is the PIN verifier. Current application code accepts salted
scrypt and legacy SHA-256 verifiers. Generate new values with the scrypt helper;
do not downgrade to an unsalted SHA-256 value.

Passkey and email-code sessions use a separate `ADMIN_SESSION_SECRET`. Hardened
PIN-cookie signing binds both that independent secret and the full PIN verifier.
Verify that the candidate includes this behavior before executing the plan;
older revisions use a different signing path. Choose the intended scope explicitly:

- **PIN replacement only:** change the PIN verifier and retain the independent
  session secret. On the hardened revision this invalidates existing PIN-cookie
  tokens; passkey/email-code sessions retain their normal expiration.
- **PIN replacement and session invalidation:** separately prepare a new independent
  session secret meeting the application's security requirements. Include both
  changes and the expected staff reauthentication in the approved release plan.
  Rotating the session secret invalidates both PIN-cookie and passkey/email-code
  sessions on the hardened revision.

Do not derive the session secret from the PIN or its verifier. Restoring an old
session secret can re-enable previously issued tokens that have not expired;
therefore a rollback must consider the incident's security reason as well as
availability. Preserve legacy-domain passkeys until replacement access is proven.
The first promotion of the hardened signing behavior invalidates older PIN-cookie
tokens even if neither secret value changes. Users can sign in again; passkey
credentials are not deleted by that signing change.

## 2. Prepare a verifier offline

Use a trusted local terminal with input recording disabled. Enter the new PIN
only at the hidden prompts; do not pass it through argv, an environment variable,
a transcript, a temporary JSON request body, or chat. The helper asks twice and
refuses empty input, mismatched confirmation, insecure input fallback, and values
longer than the browser's 64 UTF-16-unit limit. It refuses an interactive stdout
so the verifier does not appear in the terminal transcript.

From the repository root, create an operator-private temporary file and redirect
only the verifier into it:

```bash
umask 077
PIN_HASH_FILE="$(mktemp "${TMPDIR:-/tmp}/tho-pin-verifier.XXXXXX")"
if python3 scripts/generate_admin_pin_hash.py > "$PIN_HASH_FILE"; then
  printf 'Prepared private verifier file: %s\n' "$PIN_HASH_FILE"
else
  rm -f "$PIN_HASH_FILE"
  unset PIN_HASH_FILE
  false
fi
```

Do not display, copy into a ticket, or commit that file. Record only its private
location in the operator's local session. Stop on any error; an empty output file
is not a prepared verifier. Clean up the file if the rotation is abandoned or after
the approved secret update succeeds. Keep the PIN itself in the approved password
manager/recovery channel, not in this file.

## 3. Prepare the exact change for approval

Read-only metadata checks may be performed without revealing secret values or
switching the active gcloud configuration:

```bash
gcloud secrets describe admin-pin-hash --project=tho-ai-agent
gcloud secrets versions list admin-pin-hash --project=tho-ai-agent
gcloud run services describe project-go-forward --project=tho-ai-agent \
  --region=us-central1 --format='json(status.traffic,status.latestReadyRevisionName)'
```

Record the current serving revision and commit, previous secret version numbers,
the required session-invalidation scope, intended candidate secret bindings,
staff verification owner, and rollback conditions. Inspect only secret binding
names/version metadata when resolving a revision; do not dump its full environment.

Have an operator review the exact Secret Manager update and candidate deployment
steps before execution. Read the verifier directly from the prepared file rather
than echoing its contents. Bind the candidate to the reviewed numeric secret
versions so the plan identifies the actual credential set. A `latest` alias or
`latestReadyRevisionName` is not enough to identify the approved target.

The regular main workflow builds a zero-traffic candidate and uses its configured
secret bindings. Account for later workflow runs before selecting numeric bindings
for a recovery release; do not assume they remain pinned by the normal workflow.
Follow [RUNBOOK.md](RUNBOOK.md) for an explicit candidate/commit verification and
separately approved traffic promotion. Do not use `--to-latest`.

## 4. Verify without disclosing credentials

Before promotion, the designated human tests the approved candidate in the browser:

1. New PIN signs in and opens the required staff tools.
2. Previous PIN is rejected without triggering repeated lockouts.
3. A working passkey and the agreed recovery path still work.
4. For PIN-only scope, a pre-rotation PIN session is rejected and an unexpired
   passkey/email-code session still works. For session-secret rotation scope,
   both pre-rotation session types are rejected by the candidate.

Record pass/fail results, not PINs, verifiers, session cookies, screenshots of
secrets, or customer data. Candidate testing alone does not prove the production
revision changed. After the approved promotion, repeat the agreed staff checks and
read the public serving identity:

```bash
curl -fsS https://www.texashomeoutlet.com/healthz/
```

Compare the returned commit with the approved revision. A successful liveness
request is not authentication acceptance. Review startup/auth error counts through
an authorized log viewer without copying raw secrets or customer content into the
handoff.

## 5. Rollback and closeout

Use only the previously reviewed rollback revision/secret-version plan, with its
security implications understood. A compromised credential must not be restored
merely to make a login check green. Never reset or remove passkeys as incidental
cleanup. Direct traffic or secret changes remain within the applicable approval.

Remove the operator-owned temporary verifier file after use. Record the operator,
time, secret version numbers, candidate and serving revisions, expected commit,
verification results, session-invalidation scope and rollback outcome. State
whether sessions were retained or invalidated; do not automatically claim the latter.
Any staff notification is a separately approved outward message.

## Offline rehearsal

```bash
python -m pytest tests/test_admin_pin_generator_cli.py tests/test_admin_pin_kdf.py -q
```

These tests use synthetic PINs and mocked terminal input, including cancellation
and fallback errors. They prove local preparation and application-verifier
compatibility, not a completed production credential rotation or staff acceptance.
