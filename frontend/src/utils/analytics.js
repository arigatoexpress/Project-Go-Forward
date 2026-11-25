import { campaignPageLocation, getJourneyAttribution, restoreCampaignQuery } from './attribution';
import { isInventoryCategoryPath } from './inventoryCategoryRoutes';
import { GOOGLE_CLICK_FIELDS, GOOGLE_CLICK_ID_RE } from './utm';

// Fire-and-forget first-party funnel events. The same events are forwarded to
// Google only after explicit consent, with PII-shaped fields stripped.
const GOOGLE_EVENT_NAMES = {
  lead_captured: 'generate_lead',
  appointment_booked: 'schedule_appointment',
  page_viewed: 'page_view',
  phone_clicked: 'click_to_call',
};

const ADS_CONVERSION_LABELS = {
  lead_captured: 'GOOGLE_ADS_CONVERSION_LABEL_LEAD',
  phone_clicked: 'GOOGLE_ADS_CONVERSION_LABEL_CALL',
  thank_you_viewed: 'GOOGLE_ADS_CONVERSION_LABEL_THANKYOU',
};

const ADS_ID_RE = /^AW-\d{4,20}$/;
const ADS_LABEL_RE = /^[A-Za-z0-9_-]{1,80}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const THANK_YOU_KEY = 'tho_thank_you_pending';

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

function readTagConfig() {
  if (typeof document === 'undefined') return {};
  try {
    const el = document.getElementById('tho-analytics-config');
    if (!el) return {};
    const parsed = JSON.parse(el.textContent || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function fireAdsConversion(event) {
  try {
    if (typeof window === 'undefined') return;
    if (window.__THO_ANALYTICS_CONSENT__ !== 'granted' || typeof window.gtag !== 'function') return;
    const labelKey = ADS_CONVERSION_LABELS[event];
    if (!labelKey) return;
    const config = readTagConfig();
    const id = config.GOOGLE_ADS_CONVERSION_ID;
    const label = config[labelKey];
    if (typeof id !== 'string' || !ADS_ID_RE.test(id)) return;
    if (typeof label !== 'string' || !ADS_LABEL_RE.test(label)) return;
    window.gtag('event', 'conversion', { send_to: `${id}/${label}` });
  } catch {
    // Ads attribution is strictly best-effort.
  }
}

function bytesToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * SHA-256 the normalized email and attach only that hash to Google Ads.
 * Consent, the enhanced-conversions flag, and a real email are all required
 * before any hash is computed. Raw email, phone, and name are never sent.
 */
export async function prepareEnhancedConversion(email) {
  try {
    if (typeof window === 'undefined') return;
    if (window.__THO_ANALYTICS_CONSENT__ !== 'granted') return;
    if (readTagConfig().enhanced_conversions !== true) return;
    if (typeof window.gtag !== 'function') return;
    const normalized = String(email || '').trim().toLowerCase();
    if (!EMAIL_RE.test(normalized)) return;
    const subtle = window.crypto?.subtle;
    if (!subtle?.digest) return;
    const digest = await subtle.digest('SHA-256', new TextEncoder().encode(normalized));
    const hash = bytesToHex(digest);
    if (!/^[a-f0-9]{64}$/.test(hash)) return;
    window.gtag('set', 'user_data', { sha256_email_address: hash });
  } catch {
    // A hashing failure must not block the lead or send the raw address.
  }
}

export function armThankYouConversion() {
  try {
    sessionStorage.setItem(THANK_YOU_KEY, '1');
  } catch {
    // The thank-you page simply will not fire without the one-shot token.
  }
}

/**
 * Fire the thank-you page view and Ads conversion once for a successful submit.
 * A refresh or a direct visit has no token, so it does not fire again.
 */
export function consumeThankYouConversion() {
  let pending = false;
  try {
    pending = sessionStorage.getItem(THANK_YOU_KEY) === '1';
    if (pending) sessionStorage.removeItem(THANK_YOU_KEY);
  } catch {
    return;
  }
  if (!pending) return;
  trackEvent('page_viewed', { page: 'thank-you', page_path: '/thank-you' });
  fireAdsConversion('thank_you_viewed');
}

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
      if (ADS_CONVERSION_LABELS[event]) fireAdsConversion(event);
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
