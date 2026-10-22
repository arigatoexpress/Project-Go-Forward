import { describe, expect, it } from 'vitest';
import { isUnpricedListing, publicPriceText } from '../utils/publicPrice';

describe('public price display', () => {
  it('keeps a real advertised price', () => {
    const home = { display_price: '$129,900', price_value: 0 };
    expect(isUnpricedListing(home)).toBe(false);
    expect(publicPriceText(home)).toBe('$129,900');
  });

  it('treats a blank, zero, or call-for-price label as Call for Price', () => {
    for (const display_price of ['', '   ', '$0', '0', '$0.00', '$0.0', 'Call for price', 'Call for Pricing']) {
      const home = { display_price, price_value: 88000 };
      expect(isUnpricedListing(home)).toBe(true);
      expect(publicPriceText(home)).toBe('Call for Price');
    }
  });

  it('reads a nested pricing label and does not invent a price from price_value', () => {
    expect(publicPriceText({ pricing: { display_price: '$80,000' }, price_value: 1 })).toBe('$80,000');
    expect(publicPriceText({ price_value: 88000 })).toBe('Call for Price');
    expect(publicPriceText({ pricing: { display_price: '$0' }, price_value: 88000 })).toBe('Call for Price');
  });
});
