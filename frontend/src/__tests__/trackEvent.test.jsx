import { describe, it, expect, vi, afterEach } from 'vitest';
import { trackEvent } from '../pages/InventoryBrowse';
import { attachPhoneClickTracking, isPublicAnalyticsPath } from '../utils/analytics';

// Regression guard: analytics events must go to the dedicated /api/analytics
// sink (via sendBeacon), NOT /api/contact — which validated name/phone and
// silently dropped every event.

describe('trackEvent', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = '';
    sessionStorage.clear();
    window.history.replaceState({}, '', '/');
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

  it('uses one anonymous journey id per tab and never forwards it to Google', () => {
    sessionStorage.clear();
    vi.stubGlobal('navigator', {});
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true }));
    const gtag = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    window.gtag = gtag;
    window.__THO_ANALYTICS_CONSENT__ = 'granted';

    trackEvent('page_viewed', { page: 'inventory' });
    trackEvent('phone_clicked', { placement: 'hero' });

    const payloads = fetchMock.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(payloads[0].journey_id).toMatch(/^j_[0-9a-f]{32}$/);
    expect(payloads[1].journey_id).toBe(payloads[0].journey_id);
    for (const [, , params] of gtag.mock.calls) {
      expect(params).not.toHaveProperty('journey_id');
    }
  });

  it('captures every delegated public click-to-call conversion', () => {
    const beacon = vi.fn(() => true);
    const gtag = vi.fn();
    vi.stubGlobal('navigator', { sendBeacon: beacon });
    window.gtag = gtag;
    window.__THO_ANALYTICS_CONSENT__ = 'granted';
    window.history.replaceState({}, '', '/inventory');
    document.body.innerHTML = '<a href="tel:+12813243020"><span>Call now</span></a>';

    const detach = attachPhoneClickTracking(document);
    document.querySelector('span').click();
    detach();

    expect(beacon).toHaveBeenCalledTimes(1);
    expect(gtag).toHaveBeenCalledWith('event', 'phone_clicked', {
      page_path: '/inventory',
      placement: 'public_phone_link',
      tho_event: 'phone_clicked',
    });
  });

  it('does not record operator phone links as storefront conversions', () => {
    const beacon = vi.fn(() => true);
    vi.stubGlobal('navigator', { sendBeacon: beacon });
    window.history.replaceState({}, '', '/crm');
    document.body.innerHTML = '<a href="tel:+12813243020">Call lead</a>';

    const detach = attachPhoneClickTracking(document);
    document.querySelector('a').click();
    detach();

    expect(beacon).not.toHaveBeenCalled();
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

  it('attaches the landing campaign to lead and appointment events without PII', () => {
    sessionStorage.clear();
    window.history.replaceState({}, '', (
      '/inventory?utm_source=google&utm_medium=cpc'
      + '&utm_campaign=tho_search_high_intent_huffman'
      + '&gclid=test-gclid-123456&wbraid=WBRAID_valid1&gbraid=GBRAID_valid1'
    ));
    trackEvent('page_viewed', { page_path: '/inventory' });
    window.history.pushState({}, '', '/appointments');

    vi.stubGlobal('navigator', {});
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true }));
    const gtag = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    window.gtag = gtag;
    window.__THO_ANALYTICS_CONSENT__ = 'granted';

    trackEvent('lead_captured', {
      source: 'inventory_quote',
      type: 'quote',
      email: 'buyer@example.com',
      phone: '2813243020',
      name: 'Buyer',
      referrer: 'https://ads.example/?email=buyer@example.com',
    });
    trackEvent('appointment_booked', {
      source: 'website',
      intent: 'showroom_visit',
      email: 'buyer@example.com',
      phone: '2813243020',
    });

    const leadParams = gtag.mock.calls.find((call) => call[1] === 'generate_lead')[2];
    const visitParams = gtag.mock.calls.find((call) => call[1] === 'schedule_appointment')[2];
    for (const params of [leadParams, visitParams]) {
      expect(params).toMatchObject({
        utm_source: 'google',
        utm_medium: 'cpc',
        utm_campaign: 'tho_search_high_intent_huffman',
        gclid: 'test-gclid-123456',
        wbraid: 'WBRAID_valid1',
        gbraid: 'GBRAID_valid1',
      });
      expect(params.page_location).toContain('/appointments');
      expect(params.page_location).toContain('utm_campaign=tho_search_high_intent_huffman');
      expect(params.page_location).toContain('gclid=test-gclid-123456');
      expect(params).not.toHaveProperty('email');
      expect(params).not.toHaveProperty('phone');
      expect(params).not.toHaveProperty('name');
      expect(params).not.toHaveProperty('journey_id');
      expect(params).not.toHaveProperty('referrer');
      expect(JSON.stringify(params)).not.toContain('buyer@example.com');
    }

    const payloads = fetchMock.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(payloads.map((payload) => payload.event)).toEqual([
      'lead_captured',
      'appointment_booked',
    ]);
    for (const payload of payloads) {
      expect(payload.journey_id).toMatch(/^j_[0-9a-f]{32}$/);
      expect(payload.utm_campaign).toBe('tho_search_high_intent_huffman');
      expect(payload.gclid).toBe('test-gclid-123456');
      expect(payload.path).toBe('/appointments');
    }
    expect(payloads[0].journey_id).toBe(payloads[1].journey_id);
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

describe('isPublicAnalyticsPath', () => {
  it('allows public and canonical inventory paths', () => {
    expect(isPublicAnalyticsPath('/')).toBe(true);
    expect(isPublicAnalyticsPath('/appointments')).toBe(true);
    expect(isPublicAnalyticsPath('/inventory-detail/123/model/')).toBe(true);
    expect(isPublicAnalyticsPath('/homes/44490-pre-owned-big-blue')).toBe(true);
    expect(isPublicAnalyticsPath('/manufactured-homes-in-humble-tx')).toBe(true);
    expect(isPublicAnalyticsPath('/single-wide')).toBe(true);
    expect(isPublicAnalyticsPath('/double-wide/')).toBe(true);
  });

  it('fails closed for admin, API, and unknown paths', () => {
    expect(isPublicAnalyticsPath('/crm')).toBe(false);
    expect(isPublicAnalyticsPath('/analytics')).toBe(false);
    expect(isPublicAnalyticsPath('/api/admin/leads')).toBe(false);
    expect(isPublicAnalyticsPath('/unknown')).toBe(false);
  });
});
