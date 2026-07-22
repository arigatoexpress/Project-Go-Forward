const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function normalizeOptionalEmail(value) {
  const normalized = typeof value === 'string' ? value.trim() : '';
  return EMAIL_PATTERN.test(normalized) ? normalized : undefined;
}
