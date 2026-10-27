import { describe, expect, it } from 'vitest';
import { listingDocumentTitle, listingMetaDescription } from '../seoCopy';

const LONG_MODEL = (
  'The Promotional Series / The Exceptionally Long Model Name '
  + 'That Should Never Fit In A Browser Title FAC99999XYZ'
);

describe('listing document titles', () => {
  it('keeps a short model name, the city, and the brand', () => {
    const title = listingDocumentTitle('PRE-OWNED / Big Blue');
    expect(title).toBe('PRE-OWNED / Big Blue — Texas Home Outlet, Huffman, TX');
    expect(title.length).toBeLessThanOrEqual(60);
  });

  it('drops the city, then a stock code, then a series banner, before cutting the model', () => {
    expect(listingDocumentTitle('Premier / The Dauphine 3260-H-32005')).toBe(
      'Premier / The Dauphine 3260-H-32005 — Texas Home Outlet',
    );
    expect(listingDocumentTitle('The Promotional Series / The Rosewood FAC28644A')).toBe(
      'The Promotional Series / The Rosewood — Texas Home Outlet',
    );
    expect(listingDocumentTitle('NEW YEAR CLEARANCE SALE / TRU Single Section Delight')).toBe(
      'TRU Single Section Delight — Texas Home Outlet',
    );
    expect(listingDocumentTitle('NEW YEAR CLEARANCE SALE / TRU Single Section Glory')).toBe(
      'TRU Single Section Glory — Texas Home Outlet',
    );
  });

  it('clips a pathologically long model name on a word boundary within 60 characters', () => {
    const title = listingDocumentTitle(LONG_MODEL);
    expect(title).toBe('The Promotional Series / The… — Texas Home Outlet');
    expect(title.length).toBeLessThanOrEqual(60);
    expect(title.startsWith('The Promotional Series')).toBe(true);
    expect(title.endsWith('Texas Home Outlet')).toBe(true);

    const unbroken = listingDocumentTitle('X'.repeat(80));
    expect(unbroken).toBe(`${'X'.repeat(39)}… — Texas Home Outlet`);
    expect(unbroken.length).toBeLessThanOrEqual(60);

    const description = listingMetaDescription(LONG_MODEL, [
      '4 bed',
      '2 bath',
      '32x80',
      'Champion Homes',
      '$189,900',
    ]);
    expect(description.length).toBeLessThanOrEqual(155);
    expect(description).toContain('Texas Home Outlet');
    expect(description.endsWith('Call (281) 324-3020.')).toBe(true);

    const fitting = listingMetaDescription('PRE-OWNED / Big Blue', [
      '3 bed',
      '2 bath',
      'Pre-Owned',
      'Call for Price',
    ]);
    expect(fitting).toBe(
      'PRE-OWNED / Big Blue (3 bed, 2 bath, Pre-Owned, Call for Price) '
      + 'at Texas Home Outlet in Huffman, TX. Call (281) 324-3020.',
    );
    expect(fitting.length).toBeLessThanOrEqual(155);
  });
});
