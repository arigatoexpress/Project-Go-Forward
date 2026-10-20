import facts from './cityLocalFacts.json';
import { listingPath } from '../utils/listingRoutes';

export const CITY_LISTING_LIMIT = Number(facts.listing_limit) || 8;
export const LOCATION_INDEX = facts.index;

export function citySlug(city) {
  return String(city || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function cityFactsForSlug(slug) {
  return facts.cities[slug] || null;
}

export function cityPagePath(city) {
  return `/manufactured-homes-in-${citySlug(city)}-tx`;
}

export function paragraphParts(text) {
  const source = String(text || '');
  const re = /\[([^\]]+)\]\((\/[^)\s]+)\)/g;
  const parts = [];
  let last = 0;
  for (const match of source.matchAll(re)) {
    if (match.index > last) {
      parts.push({ type: 'text', text: source.slice(last, match.index) });
    }
    const href = match[2];
    if (href.startsWith('/') && !href.startsWith('//')) {
      parts.push({ type: 'link', text: match[1], href });
    } else {
      parts.push({ type: 'text', text: match[0] });
    }
    last = match.index + match[0].length;
  }
  if (last < source.length) {
    parts.push({ type: 'text', text: source.slice(last) });
  }
  return parts;
}

export function cappedListingLinks(homes, limit = CITY_LISTING_LIMIT) {
  const rows = [];
  const seen = new Set();
  for (const home of homes || []) {
    const path = listingPath(home);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    rows.push({ path, label: home.model_name || 'Manufactured home' });
  }
  rows.sort((a, b) => a.path.localeCompare(b.path));
  return rows.slice(0, limit);
}
