// Public manufacturer label. A blank value or a catalog placeholder is not a
// factory name, so the storefront leaves it off instead of printing "Unknown".

const PLACEHOLDER_MANUFACTURERS = new Set([
  'unknown',
  'n/a',
  'na',
  'n.a.',
  'tbd',
  'none',
  'null',
  'undefined',
  'various',
]);

export function publicManufacturer(value) {
  const text = String(value ?? '').trim();
  if (!text || PLACEHOLDER_MANUFACTURERS.has(text.toLowerCase())) return '';
  return text;
}

// Blank stays on the caller's fallback (the dealership name on a kicker).
// A placeholder such as "Unknown" does not become that fallback.
export function publicManufacturerLine(value, fallback = '') {
  const raw = String(value ?? '').trim();
  if (!raw) return fallback;
  return publicManufacturer(raw);
}
