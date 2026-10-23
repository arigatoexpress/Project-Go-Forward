import { friendlyModelName, isActiveHome, staffVisibleHomes, statusLabel, stockLabel } from './inventoryDisplay';
import { listingFloorplanImages, listingPagePhotos, rewriteMediaUrl } from './listingRoutes';

// Which homes still need a photo a customer can see.
// A floor plan is not a photo. A manufacturer picture that fails to load
// (hidden on the public page) is not a photo. A picture that does load
// stays as-is — staff are not asked to replace it or to invent one.

const PLACEHOLDER_RE = /(?:^|\/)tex-icon\.svg$/i;
const STAFF_PHOTO_RE = /\/api\/inventory\/photos\//i;
const PHOTO_NAME_RE = /\.(jpe?g|png|webp|gif)$/i;

/** A file the uploader can send. Phones sometimes leave the type blank. */
export function isUploadablePhotoFile(file) {
  if (!file) return false;
  const type = String(file.type || '').toLowerCase().split(';')[0].trim();
  if (type === 'image/heic' || type === 'image/heif') return false;
  if (type.startsWith('image/')) return true;
  return PHOTO_NAME_RE.test(String(file.name || ''));
}

export function blockedPhotoMessage(files) {
  const picked = Array.from(files || []).filter(Boolean);
  const heic = picked.some((file) => /heic|heif/i.test(`${file.type || ''} ${file.name || ''}`));
  if (heic) return 'That photo is in HEIC format. Tap Take a photo, or choose a JPG.';
  return 'Those files were not photos. Use JPG, PNG, WebP, or GIF.';
}

export const PHOTO_GAP_LABELS = {
  floorplan: 'Only a floor plan',
  missing: 'No photo yet',
  hidden: 'Picture did not load',
};

export const PHOTO_GAP_HINTS = {
  floorplan: 'Customers see a floor plan for this home, not a photo. Add a picture you took.',
  missing: 'This home has no photo yet. Add a picture you took.',
  hidden: 'The picture on the website did not load, so customers cannot see it. Add a picture you took.',
};

function asSet(value) {
  if (value instanceof Set) return value;
  return new Set(Array.isArray(value) ? value : []);
}

function isPlaceholderUrl(url) {
  const path = String(url || '').split(/[?#]/)[0];
  return PLACEHOLDER_RE.test(path) || path.toLowerCase().endsWith('.svg');
}

/** Photos a shopper can be shown, excluding floor plans and the site icon. */
export function customerPhotoUrls(home) {
  if (!home) return [];
  const plans = new Set(
    listingFloorplanImages(home).map((url) => String(url).replace(/\/$/, '')),
  );
  const seen = new Set();
  const urls = [];
  for (const raw of listingPagePhotos(home)) {
    const url = rewriteMediaUrl(raw);
    if (!url || seen.has(url) || isPlaceholderUrl(url)) continue;
    if (plans.has(url.replace(/\/$/, ''))) continue;
    seen.add(url);
    urls.push(url);
  }
  return urls;
}

export function hasStaffListingPhoto(home) {
  if (!home || home.has_staff_photos) return Boolean(home?.has_staff_photos);
  return customerPhotoUrls(home).some((url) => STAFF_PHOTO_RE.test(url));
}

/**
 * Why this home still needs a photo, or null when customers can already see one.
 * `failedUrls` holds picture addresses the browser could not load.
 */
export function photoGapReason(home, failedUrls) {
  if (!home || hasStaffListingPhoto(home)) return null;
  const urls = customerPhotoUrls(home);
  if (urls.length === 0) {
    return listingFloorplanImages(home).length > 0 ? 'floorplan' : 'missing';
  }
  const failed = asSet(failedUrls);
  if (urls.every((url) => failed.has(url))) return 'hidden';
  return null;
}

export function staffPhotoHomeName(home) {
  const raw = String(home?.model_name || home?.name || '').trim();
  const friendly = friendlyModelName(raw);
  if (!raw.includes('/')) return friendly;
  // "Skyliner / 4732B" would otherwise collapse to the plan code alone.
  if (/\d/.test(friendly) && friendly.length <= 16) {
    return raw.split('/').map((part) => part.trim()).filter(Boolean).join(' ');
  }
  return friendly;
}

export function staffPhotoHomeDetails(home, failedUrls) {
  const gap = photoGapReason(home, failedUrls);
  const orderable = home?.inventory_kind === 'orderable_floorplan' || home?.is_orderable === true;
  return [
    stockLabel(home),
    orderable ? 'Orderable plan' : '',
    home && !isActiveHome(home) ? statusLabel(home.status) : '',
    gap ? PHOTO_GAP_LABELS[gap] : '',
  ].filter(Boolean).join(' · ');
}

export function staffPhotoHomeLabel(home, failedUrls) {
  const name = staffPhotoHomeName(home);
  const details = staffPhotoHomeDetails(home, failedUrls);
  return details ? `${name} (${details})` : name;
}

export function homeMatchesPhotoQuery(home, query, failedUrls) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return true;
  const haystack = [
    staffPhotoHomeName(home),
    home?.model_name,
    home?.name,
    home?.manufacturer,
    home?.stock_number,
    home?.serial_number,
    staffPhotoHomeDetails(home, failedUrls),
  ].join(' ').toLowerCase();
  return haystack.includes(needle);
}

export function photoHomeListMessage({
  listedCount = 0,
  visibleCount = 0,
  onlyNeedsPhotos = false,
  checkingPhotos = false,
  query = '',
  loadError = '',
} = {}) {
  if (visibleCount > 0 || loadError) return '';
  if (listedCount === 0) {
    return 'No homes for sale right now. Tick “Include sold/archived homes” to see the rest.';
  }
  if (String(query || '').trim()) return 'No homes match. Try fewer letters.';
  if (onlyNeedsPhotos && checkingPhotos) return 'Looking for homes that need photos…';
  if (onlyNeedsPhotos) return 'Every home already has a photo customers can see.';
  return 'No homes match. Try fewer letters.';
}

export function visiblePhotoHomes(homes, {
  query = '',
  onlyNeedsPhotos = false,
  includeInactive = false,
  failedUrls,
} = {}) {
  return staffVisibleHomes(homes, { includeInactive })
    .filter((home) => (
      (!onlyNeedsPhotos || photoGapReason(home, failedUrls))
      && homeMatchesPhotoQuery(home, query, failedUrls)
    ))
    .sort((a, b) => staffPhotoHomeName(a).localeCompare(staffPhotoHomeName(b), undefined, { sensitivity: 'base' }));
}

/**
 * Public catalog homes are what customers see (including orderable plans).
 * Staff-only records are added when that id is not already on the website.
 * A staff upload flag wins so a saved photo is not listed as still needed.
 */
export function mergeStaffAndPublicHomes(publicHomes, staffHomes) {
  const staffById = new Map();
  for (const home of staffHomes || []) {
    if (!home || home.duplicate_of) continue;
    const id = String(home.id ?? '').trim();
    if (id) staffById.set(id, home);
  }
  const seen = new Set();
  const merged = [];
  for (const home of publicHomes || []) {
    if (!home) continue;
    const id = String(home.id ?? '').trim();
    if (!id) continue;
    seen.add(id);
    const staff = staffById.get(id);
    if (staff?.has_staff_photos && !home.has_staff_photos) {
      const staffPhotos = Array.isArray(staff.real_photos) ? staff.real_photos : [];
      merged.push({
        ...home,
        has_staff_photos: true,
        real_photos: staffPhotos.length > 0 ? staffPhotos : home.real_photos,
      });
    } else {
      merged.push(home);
    }
  }
  for (const [id, home] of staffById) {
    if (!seen.has(id)) merged.push(home);
  }
  return merged;
}

/** The next picture to test. One at a time per home: stop when any picture loads. */
export function photoUrlsToCheck(home, failedUrls, okUrls, gaveUpUrls) {
  if (!home || hasStaffListingPhoto(home)) return [];
  const urls = customerPhotoUrls(home);
  if (urls.length === 0) return [];
  const failed = asSet(failedUrls);
  const ok = asSet(okUrls);
  const gaveUp = asSet(gaveUpUrls);
  if (urls.some((url) => ok.has(url))) return [];
  const pending = urls.filter((url) => !failed.has(url) && !ok.has(url) && !gaveUp.has(url));
  return pending.slice(0, 1);
}

async function mapPool(items, limit, worker) {
  let index = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (index < items.length) {
      const current = items[index];
      index += 1;
      await worker(current);
    }
  });
  await Promise.all(runners);
}

/**
 * Load customer-facing pictures the page already believes are real.
 * `true` loaded, `false` failed, `null` gave up (slow or cancelled) and is
 * not treated as a missing photo.
 */
export function checkPhotoUrl(url, {
  ImageCtor = globalThis.Image,
  timeoutMs = 8000,
  signal,
} = {}) {
  if (!url || typeof ImageCtor !== 'function') return Promise.resolve(null);
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve(null);
      return;
    }
    let settled = false;
    let timer = 0;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      resolve(value);
    };
    const onAbort = () => finish(null);
    const img = new ImageCtor();
    img.onload = () => finish(true);
    img.onerror = () => finish(false);
    timer = setTimeout(() => finish(null), timeoutMs);
    signal?.addEventListener('abort', onAbort, { once: true });
    try {
      img.src = url;
    } catch {
      finish(null);
    }
  });
}

export async function checkCustomerPhotos(homes, {
  failedUrls,
  okUrls,
  gaveUpUrls,
  isCancelled = () => false,
  onFailed,
  check = checkPhotoUrl,
  concurrency = 4,
  signal,
} = {}) {
  const failed = asSet(failedUrls);
  const ok = asSet(okUrls);
  const gaveUp = asSet(gaveUpUrls);
  while (!isCancelled() && !signal?.aborted) {
    const pending = [];
    for (const home of homes || []) {
      for (const url of photoUrlsToCheck(home, failed, ok, gaveUp)) {
        if (!pending.includes(url)) pending.push(url);
      }
    }
    if (pending.length === 0) break;
    await mapPool(pending, concurrency, async (url) => {
      if (isCancelled() || signal?.aborted) return;
      const result = await check(url, { signal });
      if (isCancelled() || signal?.aborted) return;
      if (result === true) ok.add(url);
      else if (result === false) {
        failed.add(url);
        onFailed?.(url);
      } else gaveUp.add(url);
    });
  }
  return { failed, ok, gaveUp };
}
