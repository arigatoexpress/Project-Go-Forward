// Shared display rules for staff inventory lists (Manage Homes + Photos), so
// both pages show the same homes with the same names.

const INACTIVE_STATUSES = new Set(['SOLD', 'RETIRED', 'ARCHIVED']);

const STATUS_LABELS = {
  AVAILABLE: 'For sale',
  PENDING: 'Pending',
  RESERVED: 'On hold',
  SOLD: 'Sold',
  RETIRED: 'Removed from website',
  ARCHIVED: 'Archived',
};

export function isActiveHome(home) {
  return !INACTIVE_STATUSES.has(String(home?.status || 'AVAILABLE').trim().toUpperCase());
}

export function statusLabel(status) {
  const key = String(status || 'AVAILABLE').trim().toUpperCase();
  return STATUS_LABELS[key] || String(status || '');
}

// Website titles carry series names, sale banners and plan codes
// ("The Promotional Series / The Nassau FAC28483A", "PRE-OWNED / Big Blue").
// Staff know homes by the model, so keep only that part.
export function friendlyModelName(name) {
  const raw = String(name || '').trim();
  if (!raw) return 'Unnamed home';
  let value = raw.includes('/') ? raw.split('/').pop() : raw;
  value = value
    .replace(/\b(pre-?owned|new year clearance sale)\b/gi, '')
    .replace(/\b(fac|els|slt|cee|tru|sap)\d+[a-z0-9]*\b/gi, '')
    .replace(/\b\d{4}h\d+\b/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
  return value || raw;
}

export function stockLabel(home) {
  const stock = String(home?.stock_number ?? '').trim();
  if (stock) return `Stock #${stock}`;
  const serial = String(home?.serial_number ?? '').trim();
  if (serial) return `Serial ${serial}`;
  return '';
}

export function isPreOwned(home) {
  return home?.is_new === false || /pre-?owned/i.test(String(home?.model_name || ''));
}

// One row per physical home: records the API flagged as a copy of another
// (`duplicate_of`) are hidden; the kept record carries `possible_duplicate_ids`.
export function staffVisibleHomes(homes, { includeInactive = false } = {}) {
  return (Array.isArray(homes) ? homes : []).filter((home) =>
    home
    && home.id !== undefined && home.id !== null && `${home.id}` !== ''
    && !home.duplicate_of
    && (includeInactive || isActiveHome(home)));
}

export function possibleDuplicateIds(home) {
  return Array.isArray(home?.possible_duplicate_ids) ? home.possible_duplicate_ids.filter(Boolean) : [];
}
