import { describe, expect, it } from 'vitest';
import { leadHomeHref, leadInterestLine } from '../utils/leadInterest';

describe('lead interest line', () => {
  it('names the home the same way the new-lead email does', () => {
    expect(leadInterestLine({
      home_label: 'TRU Single Section Delight',
      home_stock: 'Stock #28102',
    })).toBe('Interested in: TRU Single Section Delight (Stock #28102)');
  });

  it('says general inquiry when no home was named', () => {
    expect(leadInterestLine({})).toBe('General inquiry');
    expect(leadInterestLine(null)).toBe('General inquiry');
  });

  it('only links a stored home address', () => {
    expect(leadHomeHref({ home_url: '/homes/tru-delight' })).toBe('/homes/tru-delight');
    expect(leadHomeHref({ home_url: 'javascript:alert(1)' })).toBe('');
    expect(leadHomeHref({})).toBe('');
  });
});
