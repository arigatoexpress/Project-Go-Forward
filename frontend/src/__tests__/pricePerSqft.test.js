import { describe, it, expect } from 'vitest';
import { pricePerSqft } from '../pages/InventoryBrowse';

describe('pricePerSqft', () => {
  it('computes price per square foot rounded to the nearest dollar', () => {
    expect(pricePerSqft(150000, 1200)).toBe('$125/sqft');
    expect(pricePerSqft(100000, 750)).toBe('$133/sqft'); // 133.33 -> 133
    expect(pricePerSqft(100000, 700)).toBe('$143/sqft'); // 142.85 -> 143
  });

  it('formats with thousands separators for large per-sqft values', () => {
    expect(pricePerSqft(2000000, 1000)).toBe('$2,000/sqft');
  });

  it('returns empty string when price is missing or non-positive', () => {
    expect(pricePerSqft(0, 1200)).toBe('');
    expect(pricePerSqft(null, 1200)).toBe('');
    expect(pricePerSqft(undefined, 1200)).toBe('');
    expect(pricePerSqft(-5000, 1200)).toBe('');
  });

  it('returns empty string when sqft is missing or non-positive', () => {
    expect(pricePerSqft(150000, 0)).toBe('');
    expect(pricePerSqft(150000, null)).toBe('');
    expect(pricePerSqft(150000, undefined)).toBe('');
  });

  it('coerces numeric strings', () => {
    expect(pricePerSqft('150000', '1200')).toBe('$125/sqft');
  });
});
