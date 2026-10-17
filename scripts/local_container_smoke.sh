#!/usr/bin/env bash
# Local production-image smoke — build the Dockerfile, boot it without any
# GCP credentials, and verify the container serves the public surface.
#
# This mirrors what the candidate revision must do in deploy.yml's Verify
# step, but entirely on localhost. It exists because of the boot regression
# fixed in 45f893e (eager firestore.Client() at import time killed the
# process before /healthz could answer without ADC).
#
# Usage:
#   scripts/local_container_smoke.sh            # boot verification only
#   WITH_E2E=1 scripts/local_container_smoke.sh # also run the read-only
#                                               # Playwright suite (informational:
#                                               # data-dependent tests fail on an
#                                               # empty local backend — only
#                                               # failures NOT caused by missing
#                                               # Firestore data indicate bugs)
#
# ADMIN_PIN_HASH defaults to the public CI test fixture (same value as
# deploy.yml's test job); override with a real hash if you want admin flows.

set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="tho-local-smoke:latest"
CONTAINER="tho-local-smoke"
PORT="${PORT:-8081}"
BASE_URL="http://localhost:${PORT}"
ADMIN_PIN_HASH="${ADMIN_PIN_HASH:-2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae}"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Building $IMAGE"
docker build -q -t "$IMAGE" . >/dev/null

echo "==> Booting $CONTAINER on :$PORT (no GCP credentials)"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" -p "${PORT}:8080" \
  -e ADMIN_PIN_HASH="$ADMIN_PIN_HASH" "$IMAGE" >/dev/null

echo "==> Waiting for /healthz"
for _ in $(seq 1 30); do
  if curl -sf --max-time 3 "$BASE_URL/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -sf --max-time 5 "$BASE_URL/healthz" | grep -q '"status": *"ok"' \
  && echo "    /healthz: ok" \
  || { echo "FAIL: /healthz did not come up healthy"; exit 1; }

echo "==> 50-parallel healthz probes"
PROBE_URL="$BASE_URL" python3 scripts/probe_healthz.py

echo "==> Public surface"
for path in / /healthz /healthz/; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL$path")
  echo "    GET $path -> $code"
  [ "$code" = "200" ] || { echo "FAIL: $path returned $code"; exit 1; }
done

# /readyz exercises runtime deps; without Firestore it may be 503 — report only.
readyz=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE_URL/readyz" || true)
echo "    GET /readyz -> $readyz (503 acceptable locally: no Firestore)"

if [ "${WITH_E2E:-0}" = "1" ]; then
  echo "==> Read-only Playwright suite (informational — empty local backend)"
  (cd e2e && npm ci --silent && BASE_URL="$BASE_URL" npx playwright test) || \
    echo "    (data-dependent failures expected without Firestore data; see header comment)"
fi

echo "PASS: production image boots and serves the public surface without GCP credentials."
