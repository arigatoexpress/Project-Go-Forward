const KEY = 'tho_utm';
export const UTM_FIELDS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];
export const GOOGLE_CLICK_FIELDS = ['gclid', 'gbraid', 'wbraid'];
export const CAMPAIGN_FIELDS = [...UTM_FIELDS, ...GOOGLE_CLICK_FIELDS];
export const GOOGLE_CLICK_ID_RE = /^[A-Za-z0-9._~-]{6,200}$/;

export function captureUtmFromUrl(search = window.location.search) {
  try {
    if (sessionStorage.getItem(KEY)) return; // first-touch wins
    const params = new URLSearchParams(search);
    const out = {};
    for (const field of UTM_FIELDS) {
      const value = params.get(field);
      if (value) out[field] = value.slice(0, 200);
    }
    for (const field of GOOGLE_CLICK_FIELDS) {
      const value = params.get(field);
      if (value && GOOGLE_CLICK_ID_RE.test(value)) out[field] = value;
    }
    if (document.referrer) out.referrer = document.referrer.slice(0, 200);
    if (Object.keys(out).length) sessionStorage.setItem(KEY, JSON.stringify(out));
  } catch {
    // Storage or location can be unavailable. Capture must not break the visit.
  }
}

export function getUtmParams() {
  try {
    captureUtmFromUrl();
  } catch {
    // captureUtmFromUrl already swallows storage failures.
  }
  try {
    const stored = JSON.parse(sessionStorage.getItem(KEY) || '{}');
    return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
  } catch {
    return {};
  }
}

try {
  captureUtmFromUrl();
} catch {
  // Module import can happen before a browser location exists.
}
