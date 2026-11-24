import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import fieldMap from '../../../config/field_map.json';
import {
  PRECHECK_BEACON_URL,
  buildPrecheckBlockedReport,
  inferPacketName,
  reportPrecheckBlocked,
  resetPrecheckBeaconDedupe,
} from '../utils/precheckBeacon';

// The 2026-11-23 Homestead outage was invisible to the server because the
// browser pre-check blocked before any request. The beacon reports the block
// with template names, packet, stage and field KEYS only.

const packets = Object.entries(fieldMap.packets).map(([packet_name, p]) => ({ packet_name, templates: p.templates }));
const fullNew = fieldMap.packets.full_closing_new.templates;
const PII = ['Jane Q Buyer', '123-45-6789', 'jane@example.com', '281-555-0100', '100 Main St'];

describe('buildPrecheckBlockedReport', () => {
  it('names the packet when the selection is exactly a packet', () => {
    const r = buildPrecheckBlockedReport({
      stage: 'required', selected: fullNew, packets, missing: new Set(['manufacturer_model_home_id']),
    });
    expect(r.packet).toBe('full_closing_new');
    expect(r.templates).toEqual(fullNew);
    expect(r.missing_fields).toEqual(['manufacturer_model_home_id']);
    expect(Object.keys(r).sort()).toEqual(['missing_fields', 'packet', 'stage', 'templates']);
  });

  it('is a custom selection when templates do not match a packet', () => {
    expect(inferPacketName(packets, ['Internal_Homestead.pdf'])).toBe(null);
  });

  it('keeps only key-shaped missing entries, never values', () => {
    const r = buildPrecheckBlockedReport({
      stage: 'step1', selected: ['Internal_Homestead.pdf'], packets,
      missing: new Set(['buyer_phone', ...PII]),
    });
    expect(r.missing_fields).toEqual(['buyer_phone']);
    const body = JSON.stringify(r);
    PII.forEach(v => expect(body).not.toContain(v));
  });
});

describe('reportPrecheckBlocked', () => {
  beforeEach(() => resetPrecheckBeaconDedupe());

  const report = buildPrecheckBlockedReport({
    stage: 'required', selected: ['Internal_Homestead.pdf'], packets, missing: ['manufacturer_model_home_id'],
  });

  it('posts JSON to the beacon endpoint with same-origin credentials', () => {
    const calls = [];
    const fetchImpl = (url, opts) => { calls.push([url, opts]); return Promise.resolve({ ok: true }); };
    expect(reportPrecheckBlocked(report, { fetchImpl, now: 1000 })).toBe(true);
    expect(calls).toHaveLength(1);
    const [url, opts] = calls[0];
    expect(url).toBe(PRECHECK_BEACON_URL);
    expect(opts.method).toBe('POST');
    expect(opts.credentials).toBe('same-origin');
    expect(JSON.parse(opts.body)).toEqual(report);
  });

  it('sends an identical block once per 10 minutes', () => {
    let n = 0;
    const fetchImpl = () => { n += 1; return Promise.resolve({}); };
    reportPrecheckBlocked(report, { fetchImpl, now: 0 });
    reportPrecheckBlocked(report, { fetchImpl, now: 60_000 });
    expect(n).toBe(1);
    reportPrecheckBlocked(report, { fetchImpl, now: 11 * 60_000 });
    expect(n).toBe(2);
  });

  it('never throws, even when fetch throws or rejects', () => {
    expect(() => reportPrecheckBlocked(report, { fetchImpl: () => { throw new Error('x'); } })).not.toThrow();
    resetPrecheckBeaconDedupe();
    expect(() => reportPrecheckBlocked(report, { fetchImpl: () => Promise.reject(new Error('x')) })).not.toThrow();
  });

  it('skips empty reports', () => {
    expect(reportPrecheckBlocked({ stage: 'x', templates: [], missing_fields: [] }, { fetchImpl: () => {} })).toBe(false);
  });
});

it('every Document Center pre-check block reports to the server', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(resolve(here, '../pages/DocumentCenter.jsx'), 'utf8');
  ['step1', 'step2', 'completeness', 'required', 'quality'].forEach(stage => {
    expect(src).toContain(`reportBlocked('${stage}'`);
  });
});
