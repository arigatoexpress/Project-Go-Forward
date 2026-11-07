import {
  CAMPAIGN_FIELDS,
  GOOGLE_CLICK_FIELDS,
  GOOGLE_CLICK_ID_RE,
  getUtmParams,
} from './utm';

const JOURNEY_STORAGE_KEY = 'tho_journey_id';
const JOURNEY_ID_PATTERN = /^j_[0-9a-f]{32}$/;

function generateJourneyId() {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return `j_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}

export function getJourneyId() {
  try {
    const existing = sessionStorage.getItem(JOURNEY_STORAGE_KEY);
    if (JOURNEY_ID_PATTERN.test(existing || '')) return existing;
    const generated = generateJourneyId();
    sessionStorage.setItem(JOURNEY_STORAGE_KEY, generated);
    return generated;
  } catch {
    return generateJourneyId();
  }
}

function campaignValue(stored, field) {
  const value = stored[field];
  if (typeof value !== 'string' || !value) return '';
  if (GOOGLE_CLICK_FIELDS.includes(field) && !GOOGLE_CLICK_ID_RE.test(value)) return '';
  return value;
}

export function getJourneyAttribution() {
  const stored = getUtmParams();
  const attribution = { journey_id: getJourneyId() };
  for (const field of CAMPAIGN_FIELDS) {
    const value = campaignValue(stored, field);
    if (value) attribution[field] = value;
  }
  return attribution;
}

/**
 * Landing URL with the stored campaign query restored onto the current path.
 * Returns undefined when the address bar already has those values.
 */
export function campaignPageLocation(href) {
  const current = href || (typeof window !== 'undefined' ? window.location.href : '');
  if (!current) return undefined;
  let url;
  try {
    url = new URL(current);
  } catch {
    return undefined;
  }
  const stored = getUtmParams();
  let restored = false;
  for (const field of CAMPAIGN_FIELDS) {
    const value = campaignValue(stored, field);
    if (!value) continue;
    if (url.searchParams.get(field) !== value) {
      url.searchParams.set(field, value);
      restored = true;
    }
  }
  return restored ? url.toString() : undefined;
}

export function restoreCampaignQuery() {
  const location = campaignPageLocation();
  if (!location || typeof window === 'undefined' || !window.history?.replaceState) return location;
  try {
    const url = new URL(location);
    const next = `${url.pathname}${url.search}${url.hash}`;
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (next !== current) window.history.replaceState(window.history.state, '', next);
  } catch {
    // History can be locked. Callers still receive the restored location string.
  }
  return location;
}
