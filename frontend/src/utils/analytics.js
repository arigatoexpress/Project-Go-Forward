import { campaignPageLocation, getJourneyAttribution, restoreCampaignQuery } from './attribution';
import { isInventoryCategoryPath } from './inventoryCategoryRoutes';
import { GOOGLE_CLICK_FIELDS, GOOGLE_CLICK_ID_RE } from './utm';

// Fire-and-forget first-party funnel events. The same events are forwarded to
// Google only after explicit consent, with PII-shaped fields stripped.
const GOOGLE_EVENT_NAMES = {
  lead_captured: 'generate_lead',
  appointment_booked: 'schedule_appointment',
  page_viewed: 'page_view',
};

const PII_KEYS = new Set([
  'name', 'email', 'phone', 'address', 'message', 'notes',
  'lead_id', 'user_id', 'session_id', 'journey_id', 'referrer',
]);

const CLICK_ID_KEYS = new Set(GOOGLE_CLICK_FIELDS);
const PAGE_LOCATION_EVENTS = new Set(['page_viewed', 'lead_captured', 'appointment_booked']);

const PUBLIC_EXACT_PATHS = new Set([
  '/', '/inventory', '/locations', '/chat', '/contact', '/appointments', '/about',
  '/financing', '/faq', '/warranty', '/delivery',
]);

export function isPublicAnalyticsPath(pathname) {
  const raw = String(pathname || '/').toLowerCase();
  const path = raw.length > 1 ? raw.replace(/\/+$/, '') : raw;
  return PUBLIC_EXACT_PATHS.has(path)
    || isInventoryCategoryPath(path)
    || path.startsWith('/inventory-detail/')
    || path.startsWith('/plan/')
    || path.startsWith('/homes/')
    || path.startsWith('/manufactured-homes-in-');
}

function boundAnalyticsValue(key, value) {
  if (typeof value !== 'string') return value;
  if (key === 'page_location') return value.slice(0, 1000);
  if (CLICK_ID_KEYS.has(key)) return value.slice(0, 200);
  return value.slice(0, 100);
}

function campaignFields(attribution) {
  const fields = {};
  for (const [key, value] of Object.entries(attribution || {})) {
    if (key === 'journey_id' || PII_KEYS.has(key)) continue;
    if (typeof value === 'string' && value) fields[key] = value;
  }
  return fields;
}

function googleEventParams(event, data) {
  const params = {};
  for (const [key, value] of Object.entries(data || {})) {
    if (PII_KEYS.has(key.toLowerCase())) continue;
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(key)) continue;
    if (!['string', 'number', 'boolean'].includes(typeof value)) continue;
    if (CLICK_ID_KEYS.has(key) && (typeof value !== 'string' || !GOOGLE_CLICK_ID_RE.test(value))) {
      continue;
    }
    params[key] = boundAnalyticsValue(key, value);
  }
  if (PAGE_LOCATION_EVENTS.has(event)) {
    const pageLocation = campaignPageLocation();
    if (pageLocation) params.page_location = pageLocation.slice(0, 1000);
  }
  params.tho_event = event;
  return params;
}

export function trackEvent(event, data = {}) {
  const attribution = getJourneyAttribution();
  const campaign = campaignFields(attribution);
  try {
    const payload = JSON.stringify({
      event,
      ...data,
      ...campaign,
      journey_id: attribution.journey_id,
      path: typeof window !== 'undefined' ? window.location.pathname : undefined,
    });
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      navigator.sendBeacon('/api/analytics', new Blob([payload], { type: 'application/json' }));
    } else {
      fetch('/api/analytics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    }
  } catch {
    // Analytics must never interrupt the conversion path.
  }

  try {
    if (
      typeof window !== 'undefined'
      && window.__THO_ANALYTICS_CONSENT__ === 'granted'
      && typeof window.gtag === 'function'
      && /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(event)
    ) {
      window.gtag(
        'event',
        GOOGLE_EVENT_NAMES[event] || event,
        googleEventParams(event, { ...data, ...campaign }),
      );
    }
  } catch {
    // Third-party attribution is strictly best-effort.
  }
}

/**
 * Record the current public page once, after the visitor grants analytics.
 * The first React page view is correctly withheld until this runs.
 */
export function reportConsentedPageView(pathname) {
  try {
    if (typeof window === 'undefined') return;
    if (window.__THO_ANALYTICS_CONSENT__ !== 'granted' || typeof window.gtag !== 'function') return;
    if (!isPublicAnalyticsPath(pathname)) return;
    const pendingLocation = campaignPageLocation();
    restoreCampaignQuery();
    const campaign = campaignFields(getJourneyAttribution());
    const params = googleEventParams('page_viewed', {
      page_path: pathname,
      ...campaign,
    });
    if (pendingLocation) params.page_location = pendingLocation.slice(0, 1000);
    window.gtag('event', 'page_view', params);
  } catch {
    // Consent reporting must not block the privacy choice.
  }
}

/**
 * Track click-to-call intent from every current and future public `tel:` link.
 * Event delegation keeps the conversion contract centralized instead of
 * requiring each CTA to remember analytics wiring. The phone number is never
 * included in the event payload.
 */
export function attachPhoneClickTracking(targetRoot = document) {
  if (!targetRoot?.addEventListener) return () => {};

  const handleClick = (clickEvent) => {
    try {
      const anchor = clickEvent.target?.closest?.('a[href]');
      if (!anchor || !String(anchor.getAttribute('href') || '').toLowerCase().startsWith('tel:')) {
        return;
      }
      const pagePath = typeof window !== 'undefined' ? window.location.pathname : '/';
      if (!isPublicAnalyticsPath(pagePath)) return;

      trackEvent('phone_clicked', {
        page_path: pagePath,
        placement: anchor.dataset.analyticsPlacement || 'public_phone_link',
      });
    } catch {
      // Conversion analytics must never interfere with the native dialer.
    }
  };

  targetRoot.addEventListener('click', handleClick);
  return () => targetRoot.removeEventListener('click', handleClick);
}
