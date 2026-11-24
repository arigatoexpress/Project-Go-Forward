import { adminCsrfHeaders } from '../adminFetch';

// Tells the server when the Document Center's browser pre-check blocks
// generation. Without this, a broken pre-check (2026-11-23 Homestead outage)
// never reaches the server and leaves no log line.
//
// Privacy: only template names, the packet name, the stage and missing field
// KEYS are sent. Never form values. The server re-checks every value against
// config/field_map.json.

export const PRECHECK_BEACON_URL = '/api/documents/precheck-blocked';
const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;
const DEDUPE_MS = 10 * 60 * 1000;
const recent = new Map();

export function inferPacketName(packets, selected) {
  const sel = new Set(selected || []);
  const match = (packets || []).find(p => {
    const tpls = p?.templates || [];
    return tpls.length === sel.size && tpls.every(t => sel.has(t));
  });
  return match ? match.packet_name : null;
}

export function buildPrecheckBlockedReport({ stage, selected, packets, missing }) {
  const templates = [...new Set((selected || []).filter(t => typeof t === 'string'))].slice(0, 60);
  const missingFields = [...new Set([...(missing || [])].filter(k => typeof k === 'string' && KEY_RE.test(k)))]
    .sort()
    .slice(0, 40);
  return {
    stage,
    templates,
    packet: inferPacketName(packets, templates),
    missing_fields: missingFields,
  };
}

function dedupeKey(report) {
  return `${report.stage}|${[...report.templates].sort().join(',')}|${report.missing_fields.join(',')}`;
}

export function resetPrecheckBeaconDedupe() {
  recent.clear();
}

// Fire-and-forget. Identical reports within 10 minutes are sent once, so a
// double click is one event and "blocked twice" means two real attempts.
export function reportPrecheckBlocked(report, { fetchImpl, now = Date.now() } = {}) {
  try {
    if (!report || (report.templates.length === 0 && report.missing_fields.length === 0)) return false;
    const key = dedupeKey(report);
    const last = recent.get(key);
    if (last !== undefined && now - last < DEDUPE_MS) return false;
    recent.set(key, now);
    const doFetch = fetchImpl || (typeof fetch === 'function' ? fetch : null);
    if (!doFetch) return false;
    const p = doFetch(PRECHECK_BEACON_URL, {
      method: 'POST',
      credentials: 'same-origin',
      keepalive: true,
      headers: adminCsrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(report),
    });
    if (p && typeof p.catch === 'function') p.catch(() => {});
    return true;
  } catch {
    return false;
  }
}
