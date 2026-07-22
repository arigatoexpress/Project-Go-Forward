// Fire-and-forget first-party funnel events. The same events are forwarded to
// Google only after explicit consent, with PII-shaped fields stripped.
const GOOGLE_EVENT_NAMES = {
  lead_captured: 'generate_lead',
  appointment_booked: 'schedule_appointment',
  page_viewed: 'page_view',
};

const PII_KEYS = new Set([
  'name', 'email', 'phone', 'address', 'message', 'notes',
  'lead_id', 'user_id', 'session_id',
]);

const PUBLIC_EXACT_PATHS = new Set([
  '/', '/inventory', '/chat', '/contact', '/appointments', '/about',
  '/financing', '/faq', '/warranty', '/delivery',
]);

export function isPublicAnalyticsPath(pathname) {
  const raw = String(pathname || '/').toLowerCase();
  const path = raw.length > 1 ? raw.replace(/\/+$/, '') : raw;
  return PUBLIC_EXACT_PATHS.has(path)
    || path.startsWith('/inventory-detail/')
    || path.startsWith('/plan/')
    || path.startsWith('/manufactured-homes-in-');
}

function googleEventParams(event, data) {
  const params = {};
  for (const [key, value] of Object.entries(data || {})) {
    if (PII_KEYS.has(key.toLowerCase())) continue;
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(key)) continue;
    if (!['string', 'number', 'boolean'].includes(typeof value)) continue;
    params[key] = typeof value === 'string' ? value.slice(0, 100) : value;
  }
  params.tho_event = event;
  return params;
}

export function trackEvent(event, data = {}) {
  try {
    const payload = JSON.stringify({
      event,
      ...data,
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
        googleEventParams(event, data),
      );
    }
  } catch {
    // Third-party attribution is strictly best-effort.
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
