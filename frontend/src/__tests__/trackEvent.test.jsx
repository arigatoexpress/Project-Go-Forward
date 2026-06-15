import { describe, it, expect, vi, afterEach } from 'vitest';
import { trackEvent } from '../pages/InventoryBrowse';

// Regression guard: analytics events must go to the dedicated /api/analytics
// sink (via sendBeacon), NOT /api/contact — which validated name/phone and
// silently dropped every event.

describe('trackEvent', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sends to /api/analytics via sendBeacon', () => {
    const beacon = vi.fn(() => true);
    vi.stubGlobal('navigator', { sendBeacon: beacon });

    trackEvent('lead_captured', { home: 'Nassau' });

    expect(beacon).toHaveBeenCalledTimes(1);
    const [url, blob] = beacon.mock.calls[0];
    expect(url).toBe('/api/analytics');
    expect(blob.type).toBe('application/json');
  });

  it('falls back to fetch keepalive when sendBeacon is unavailable', () => {
    vi.stubGlobal('navigator', {});
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    trackEvent('home_view', { home: 'X' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/analytics');
    expect(opts).toMatchObject({ method: 'POST', keepalive: true });
  });

  it('never throws (analytics must not interrupt browsing)', () => {
    vi.stubGlobal('navigator', {
      sendBeacon: () => {
        throw new Error('boom');
      },
    });
    expect(() => trackEvent('whatever')).not.toThrow();
  });
});
