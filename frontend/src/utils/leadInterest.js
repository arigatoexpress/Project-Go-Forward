export function leadInterestLine(lead) {
  if (!lead || typeof lead !== 'object') return 'General inquiry';
  const label = String(lead.home_label || lead.home_model || '').trim();
  const stock = String(lead.home_stock || '').trim();
  if (!label && !stock) return 'General inquiry';
  const name = label || 'Home (model not given)';
  return stock ? `Interested in: ${name} (${stock})` : `Interested in: ${name}`;
}

export function leadHomeHref(lead) {
  const value = String(lead?.home_url || '').trim();
  if (value.startsWith('/') && !value.startsWith('//')) return value;
  try {
    const parsed = new URL(value);
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') return value;
  } catch {
    return '';
  }
  return '';
}
