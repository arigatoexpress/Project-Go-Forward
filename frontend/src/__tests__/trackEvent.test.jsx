import { describe, it, expect, vi, afterEach } from 'vitest';
import { trackEvent } from '../pages/InventoryBrowse';

// Regression guard: analytics events must go to the dedicated /api/analytics
// sink (via sendBeacon), NOT /api/contact — which validated name/phone and
// silently dropped every event.

describe('trackEvent', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    delete window.gtag;
    delete window.__THO_ANALYTICS_CONSENT__;
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

  it('forwards revenue events to Google only after consent', () => {
    const beacon = vi.fn(() => true);
    const gtag = vi.fn();
    vi.stubGlobal('navigator', { sendBeacon: beacon });
    window.gtag = gtag;

    window.__THO_ANALYTICS_CONSENT__ = 'denied';
    trackEvent('lead_captured', { home: 'Nassau', type: 'quote' });
    expect(gtag).not.toHaveBeenCalled();

    window.__THO_ANALYTICS_CONSENT__ = 'granted';
    trackEvent('lead_captured', { home: 'Nassau', type: 'quote' });
    expect(gtag).toHaveBeenCalledWith('event', 'generate_lead', {
      home: 'Nassau',
      type: 'quote',
      tho_event: 'lead_captured',
    });
  });

  it('never forwards accidental PII-shaped properties to Google', () => {
    const gtag = vi.fn();
    vi.stubGlobal('navigator', { sendBeacon: () => true });
    window.gtag = gtag;
    window.__THO_ANALYTICS_CONSENT__ = 'granted';

    trackEvent('appointment_booked', {
      source: 'website',
      email: 'buyer@example.com',
      phone: '2813243020',
      name: 'Buyer',
    });

    expect(gtag).toHaveBeenCalledWith('event', 'schedule_appointment', {
      source: 'website',
      tho_event: 'appointment_booked',
    });
  });

  it('maps SPA navigation to the GA4 page_view event', () => {
    const gtag = vi.fn();
    vi.stubGlobal('navigator', { sendBeacon: () => true });
    window.gtag = gtag;
    window.__THO_ANALYTICS_CONSENT__ = 'granted';

    trackEvent('page_viewed', { page: 'inventory', page_path: '/inventory' });

    expect(gtag).toHaveBeenCalledWith('event', 'page_view', {
      page: 'inventory',
      page_path: '/inventory',
      tho_event: 'page_viewed',
    });
  });
});
