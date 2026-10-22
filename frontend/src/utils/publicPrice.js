// Public price display. The listing's display_price field decides the label.
// A blank price or a zero dollar amount is Call for Price. A hidden
// price_value is never shown in place of that label.

const CALL_FOR_PRICE_RE = /^call\s+for\s+pric(?:e|ing)$/i;
const ZERO_AMOUNT_RE = /^0+(?:\.0+)?$/;

export const CALL_FOR_PRICE_LABEL = 'Call for Price';

export function priceSource(home) {
  if (!home || typeof home !== 'object') return '';
  if (home.display_price != null && String(home.display_price).trim() !== '') {
    return String(home.display_price).trim();
  }
  const nested = home.pricing && home.pricing.display_price;
  if (nested != null && String(nested).trim() !== '') return String(nested).trim();
  return '';
}

export function isUnpricedListing(home) {
  const text = priceSource(home);
  if (!text || CALL_FOR_PRICE_RE.test(text)) return true;
  const compact = text.replace(/[\s$,]/g, '');
  return ZERO_AMOUNT_RE.test(compact);
}

export function publicPriceText(home) {
  if (isUnpricedListing(home)) return CALL_FOR_PRICE_LABEL;
  return priceSource(home);
}
