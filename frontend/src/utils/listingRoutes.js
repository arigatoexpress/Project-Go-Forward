import { BUSINESS_CITY, BUSINESS_NAME, BUSINESS_STATE } from '../constants';
import { cityDescription, cityTitle } from '../seoCopy';
import cdnExtensionRepairs from '../../../data/manufacturer_cdn_extension_repairs.json';

const SLUG_RE = /[^a-z0-9]+/g;
export const PUBLIC_CANONICAL_ORIGIN = 'https://www.texashomeoutlet.com';
const CANONICAL_HOST_ALIASES = new Set(['texashomeoutlet.com', 'tho.sapphirealpha.xyz']);

const PHOTO_SUBJECTS = [
  ['exterior', ['exterior', 'ext', 'front']],
  ['kitchen', ['kitchen', 'kit']],
  ['living room', ['living', 'lvgrm', 'lvg']],
  ['bathroom', ['bathroom', 'bath']],
  ['bedroom', ['bedroom', 'bed']],
  ['interior', ['interior', 'int']],
  ['porch', ['porch']],
  ['utility', ['utility']],
];
const CITY_PATH_RE = /^\/manufactured-homes-in-([a-z0-9-]+)-tx\/?$/i;
const HOME_PATH_RE = /^\/homes\/([^/]+)\/?$/i;
const PLAN_PATH_RE = /^\/plan\/(\d+)(?:\/|$)/i;
const DETAIL_PATH_RE = /^\/inventory-detail\/(\d+)(?:\/|$)/i;

export function listingSlug(value) {
  const slug = String(value || '').trim().toLowerCase().replace(SLUG_RE, '-').replace(/^-+|-+$/g, '');
  return slug || 'home';
}

export function stockId(home) {
  const candidates = [home?.stock_number, home?.legacy_inventory_id, home?.id, home?.home_id];
  const numeric = candidates.map((raw) => String(raw || '').trim()).filter((value) => /^\d+$/.test(value));
  if (numeric.length) return numeric[0];
  for (const raw of candidates) {
    const value = String(raw || '').trim();
    if (value) return value;
  }
  return '';
}

export function isInstockHome(home) {
  if (!home) return false;
  if (home.is_orderable === true || home.inventory_kind === 'orderable_floorplan' || home.status === 'Orderable') {
    return false;
  }
  const kind = String(home.inventory_kind || '').trim();
  if (kind === 'available_now' || kind === 'pre_owned') return true;
  const status = String(home.status || '').toLowerCase();
  return status === 'available' || (status.includes('pre') && status.includes('owned'));
}

export function isPreownedHome(home) {
  if (!home) return false;
  if (home.is_new === false || home.inventory_kind === 'pre_owned') return true;
  const status = String(home.status || '').toLowerCase();
  if (status.includes('pre') && status.includes('owned')) return true;
  return /pre-?owned/i.test(String(home.model_name || ''));
}

export function listingPath(home) {
  if (home?.listing_url) {
    const raw = String(home.listing_url);
    try {
      return raw.startsWith('http') ? new URL(raw).pathname : raw;
    } catch {
      return raw;
    }
  }
  if (!isInstockHome(home)) {
    const detail = home?.detail_url;
    if (!detail) return '';
    try {
      return detail.startsWith('http') ? new URL(detail).pathname : detail;
    } catch {
      return detail;
    }
  }
  const identifier = stockId(home);
  if (!identifier || !/^\d+$/.test(identifier)) return '';
  return `/homes/${identifier}-${listingSlug(home.model_name)}`;
}

export function getCityLanding(pathname) {
  const match = String(pathname || '').toLowerCase().match(CITY_PATH_RE);
  if (!match) return null;
  const slug = match[1];
  const city = slug.split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
  return {
    city,
    slug,
    path: `/manufactured-homes-in-${slug}-tx`,
    heading: `Manufactured & Mobile Homes in ${city}, TX`,
    title: cityTitle(city),
    description: cityDescription(city),
  };
}

export function getHomeListingToken(pathname) {
  const match = String(pathname || '').match(HOME_PATH_RE);
  return match ? match[1] : '';
}

export function isHomeListingPath(pathname) {
  return Boolean(getHomeListingToken(pathname));
}

export function isPlanPath(pathname) {
  return PLAN_PATH_RE.test(String(pathname || ''));
}

export function isInventoryDetailPath(pathname) {
  return DETAIL_PATH_RE.test(String(pathname || ''));
}

export function isUniqueListingPath(pathname) {
  return Boolean(
    getCityLanding(pathname)
    || isHomeListingPath(pathname)
    || isPlanPath(pathname)
    || isInventoryDetailPath(pathname),
  );
}

function titleCaseSlug(value) {
  return String(value || '')
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function fallbackListingHeading(pathname) {
  const city = getCityLanding(pathname);
  if (city) return city.heading;
  const token = getHomeListingToken(pathname);
  if (token) {
    const slug = token.replace(/^\d+-/, '');
    return titleCaseSlug(slug) || 'Manufactured Home';
  }
  const plan = String(pathname || '').match(/^\/plan\/\d+(?:\/([^/]+))?(?:\/([^/]+))?/i);
  if (plan) {
    const heading = [plan[1], plan[2]].filter(Boolean).map(titleCaseSlug).join(' ');
    return heading || 'Manufactured Home Floorplan';
  }
  return null;
}

export function resolveHomeFromPath(homes, pathname) {
  const token = getHomeListingToken(pathname);
  if (!token) return null;
  const list = Array.isArray(homes) ? homes : [];
  const exactPath = `/homes/${token}`;
  const exact = list.find((home) => listingPath(home).replace(/\/$/, '') === exactPath);
  if (exact) return exact;
  const lower = token.toLowerCase();
  return list.find((home) => {
    const identifier = stockId(home).toLowerCase();
    return identifier && (lower === identifier || lower.startsWith(`${identifier}-`));
  }) || null;
}

const STOCK_PHOTO_HOSTS = new Set([
  'images.unsplash.com',
  'unsplash.com',
  'images.pexels.com',
  'pexels.com',
  'pixabay.com',
  'cdn.pixabay.com',
  'placehold.co',
  'via.placeholder.com',
  'placeholder.com',
  'picsum.photos',
  'loremflickr.com',
  'lorempixel.com',
]);

function isManufacturerCatalogPhotoUrl(url) {
  if (!url) return false;
  try {
    return /\/manufacturer\/[^/]+\/floorplan\/[^/]+\//i.test(new URL(url).pathname);
  } catch {
    return /\/manufacturer\/[^/]+\/floorplan\/[^/]+\//i.test(String(url));
  }
}

function photoHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '').toLowerCase();
  } catch {
    return '';
  }
}

export function isGenericStockPhotoUrl(url) {
  return STOCK_PHOTO_HOSTS.has(photoHost(url));
}

function embeddedUnitId(url) {
  const decoded = decodeURIComponent(String(url || ''));
  const match = decoded.match(/\/(?:dealer\/[^/]+\/inventory\/|api\/inventory\/photos\/|inventory\/)([^/?#]+)\//i);
  return match ? match[1] : '';
}

function homePhotoIds(home) {
  return [home?.id, home?.stock_number, home?.stock_id, home?.legacy_inventory_id, home?.home_id]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
}

export function isForeignUnitPhoto(url, home) {
  const unit = embeddedUnitId(url);
  if (!unit) return false;
  const ids = homePhotoIds(home);
  if (!ids.length) return false;
  // Upload paths keep this home's id, but not always its original case.
  const folded = unit.toLowerCase();
  return !ids.some((id) => id.toLowerCase() === folded);
}

/** Used homes may show only that unit's photos — never catalog or stock shots. */
export function usedHomeMayShowPhoto(url, home) {
  if (!url || !isPreownedHome(home)) return Boolean(url);
  if (isManufacturerCatalogPhotoUrl(url)) return false;
  if (isGenericStockPhotoUrl(url)) return false;
  if (isForeignUnitPhoto(url, home)) return false;
  return true;
}

// Same-stem extension siblings confirmed live on the manufacturer CDN.
// The public inventory feed (2026-10-22) had 71 of 697 image URLs return
// HTTP 403 because CloudFront/S3 keeps `.jpg`, `.JPG`, `.jpeg`, `.png`,
// and `.webp` as different objects. This table is every dead key that
// has a working sibling — not one listing.
const MANUFACTURER_CDN_HOST = 'd132mt2yijm03y.cloudfront.net';

function encodeCdnFilename(filename) {
  // Match Python urllib.parse.quote(filename, safe=""): encode the
  // characters encodeURIComponent leaves alone, using uppercase hex.
  return encodeURIComponent(filename).replace(/[!'()*]/g, (ch) => (
    `%${ch.charCodeAt(0).toString(16).toUpperCase()}`
  ));
}

export function rewriteMediaUrl(url) {
  const value = String(url || '').trim();
  if (!value) return '';
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return value;
  }
  if (parsed.hostname !== MANUFACTURER_CDN_HOST) return value;
  let key = '';
  try {
    key = decodeURIComponent(parsed.pathname).replace(/^\/+/, '');
  } catch {
    return value;
  }
  const filename = cdnExtensionRepairs[key];
  if (!filename) return value;
  const folder = key.slice(0, key.lastIndexOf('/'));
  return `${parsed.origin}/${folder}/${encodeCdnFilename(filename)}${parsed.search}${parsed.hash}`;
}

export function listingPagePhotos(home) {
  const floorplanUrls = [
    home?.floorplan_url,
    home?.floor_plan_url,
    ...(Array.isArray(home?.floorplan_urls) ? home.floorplan_urls : []),
  ].filter(Boolean).map((url) => String(url).trim().replace(/\/$/, ''));
  const isFloorplan = (url) => {
    if (!url) return false;
    const normalized = String(url).trim().replace(/\/$/, '');
    if (floorplanUrls.includes(normalized)) return true;
    const filename = decodeURIComponent(String(url).split('/').pop()?.split('?')[0] || '').toLowerCase();
    return filename.endsWith('.pdf') || filename.includes('floorplan') || filename.includes('floor-plan') || filename.includes('floor_plan');
  };
  const used = isPreownedHome(home);
  const candidates = used
    ? [home?.image_url, home?.hero_image, ...(Array.isArray(home?.real_photos) ? home.real_photos : [])]
    : [home?.image_url, ...(Array.isArray(home?.real_photos) ? home.real_photos : [])];
  const photos = [];
  const seen = new Set();
  for (const raw of candidates) {
    const url = rewriteMediaUrl(raw);
    if (!url || seen.has(url) || isFloorplan(url)) continue;
    if (used && !usedHomeMayShowPhoto(url, home)) continue;
    seen.add(url);
    photos.push(url);
  }
  if (photos.length) return photos;
  const plan = home?.floorplan_url || home?.floor_plan_url || floorplanUrls[0] || '';
  return plan ? [plan] : [];
}

export function publicCanonicalOrigin(hostname, origin) {
  const host = String(
    hostname ?? (typeof window !== 'undefined' ? window.location.hostname : ''),
  ).toLowerCase();
  const current = origin ?? (typeof window !== 'undefined' ? window.location.origin : '');
  if (CANONICAL_HOST_ALIASES.has(host)) return PUBLIC_CANONICAL_ORIGIN;
  return current || PUBLIC_CANONICAL_ORIGIN;
}

function photoFilename(url) {
  const raw = String(url || '').split('/').pop()?.split('?')[0] || '';
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function listingPhotoSubject(url) {
  const filename = photoFilename(url);
  const stem = filename.replace(/\.[^.]+$/, '').toLowerCase();
  const parts = new Set(stem.split(/[^a-z0-9]+/).filter(Boolean));
  for (const [label, tokens] of PHOTO_SUBJECTS) {
    if (tokens.some((token) => parts.has(token))) return label;
  }
  return '';
}

export function listingImageAlt(home, url, index, total, { floorplan = false } = {}) {
  const name = String(home?.model_name || 'Manufactured home').trim() || 'Manufactured home';
  if (floorplan) return `Floor plan for ${name}`;
  const subject = listingPhotoSubject(url);
  const count = Number(total) || 1;
  const position = Number(index) || 1;
  let photo;
  if (subject && count > 1) photo = `${subject} photo ${position}`;
  else if (subject) photo = `${subject} photo`;
  else if (count > 1) photo = `manufactured home photo ${position}`;
  else photo = 'manufactured home';
  return `${name} — ${photo} at ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}`;
}

export function listingShareUrl(home) {
  const origin = publicCanonicalOrigin();
  const path = listingPath(home);
  if (path) return `${origin}${path.startsWith('/') ? path : `/${path}`}`;
  const id = encodeURIComponent(home?.id || '');
  return `${origin}/inventory?home=${id}`;
}

function upsertMeta(attribute, key, content) {
  if (!content || typeof document === 'undefined') return;
  let meta = document.querySelector(`meta[${attribute}="${key}"]`);
  if (!meta) {
    meta = document.createElement('meta');
    meta.setAttribute(attribute, key);
    document.head.appendChild(meta);
  }
  meta.setAttribute('content', content);
}

export function applyDocumentHead({ title, description, canonicalPath, image } = {}) {
  if (typeof document === 'undefined') return;
  if (title) document.title = title;
  const origin = publicCanonicalOrigin();
  let canonicalHref = '';
  if (description) {
    upsertMeta('name', 'description', description);
  }
  if (canonicalPath) {
    let link = document.querySelector('link[rel="canonical"]');
    if (!link) {
      link = document.createElement('link');
      link.setAttribute('rel', 'canonical');
      document.head.appendChild(link);
    }
    canonicalHref = canonicalPath.startsWith('http') ? canonicalPath : `${origin}${canonicalPath}`;
    link.setAttribute('href', canonicalHref);
  }
  if (title) upsertMeta('property', 'og:title', title);
  if (description) upsertMeta('property', 'og:description', description);
  if (canonicalHref) upsertMeta('property', 'og:url', canonicalHref);
  upsertMeta('property', 'og:type', 'website');
  upsertMeta('property', 'og:locale', 'en_US');
  upsertMeta('property', 'og:site_name', BUSINESS_NAME);
  upsertMeta('name', 'twitter:card', 'summary_large_image');
  if (title) upsertMeta('name', 'twitter:title', title);
  if (description) upsertMeta('name', 'twitter:description', description);
  if (image) {
    const absolute = String(image).startsWith('http') ? image : `${origin}${image}`;
    upsertMeta('property', 'og:image', absolute);
    upsertMeta('name', 'twitter:image', absolute);
  }
}
