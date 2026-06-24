const KEY = 'tho_utm';
const FIELDS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];

export function captureUtmFromUrl(search = window.location.search) {
  if (sessionStorage.getItem(KEY)) return; // first-touch wins
  const p = new URLSearchParams(search);
  const out = {};
  for (const f of FIELDS) {
    const v = p.get(f);
    if (v) out[f] = v.slice(0, 200);
  }
  if (document.referrer) out.referrer = document.referrer.slice(0, 200);
  if (Object.keys(out).length) sessionStorage.setItem(KEY, JSON.stringify(out));
}

export function getUtmParams() {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || '{}');
  } catch {
    return {};
  }
}
