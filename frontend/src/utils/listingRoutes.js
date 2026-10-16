const SLUG_RE = /[^a-z0-9]+/g;
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
    title: `Manufactured & Mobile Homes in ${city}, TX | Texas Home Outlet`,
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
    const url = String(raw || '').trim();
    if (!url || seen.has(url) || isFloorplan(url)) continue;
    if (used && !usedHomeMayShowPhoto(url, home)) continue;
    seen.add(url);
    photos.push(url);
  }
  if (photos.length) return photos;
  const plan = home?.floorplan_url || home?.floor_plan_url || floorplanUrls[0] || '';
  return plan ? [plan] : [];
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
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
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
  upsertMeta('name', 'twitter:card', 'summary_large_image');
  if (title) upsertMeta('name', 'twitter:title', title);
  if (description) upsertMeta('name', 'twitter:description', description);
  if (image) {
    const absolute = String(image).startsWith('http') ? image : `${origin}${image}`;
    upsertMeta('property', 'og:image', absolute);
    upsertMeta('name', 'twitter:image', absolute);
  }
}
