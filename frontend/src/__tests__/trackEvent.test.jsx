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
    expect(isPublicAnalyticsPath('/manufactured-homes-in-humble-tx')).toBe(true);
  });

  it('fails closed for admin, API, and unknown paths', () => {
    expect(isPublicAnalyticsPath('/crm')).toBe(false);
    expect(isPublicAnalyticsPath('/analytics')).toBe(false);
    expect(isPublicAnalyticsPath('/api/admin/leads')).toBe(false);
    expect(isPublicAnalyticsPath('/unknown')).toBe(false);
  });
});
