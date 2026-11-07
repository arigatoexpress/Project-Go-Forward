import { describe, expect, it } from 'vitest';
import { publicManufacturer, publicManufacturerLine } from '../utils/publicManufacturer';

describe('public manufacturer label', () => {
  it('keeps a real factory name', () => {
    expect(publicManufacturer('Clayton Sulphur Springs')).toBe('Clayton Sulphur Springs');
    expect(publicManufacturer(' Pre-Owned ')).toBe('Pre-Owned');
  });

  it('hides placeholder factory names', () => {
    for (const value of ['Unknown', 'unknown', 'N/A', 'n.a.', 'TBD', 'none', 'null', 'undefined', 'Various']) {
      expect(publicManufacturer(value)).toBe('');
    }
    expect(publicManufacturer('')).toBe('');
    expect(publicManufacturer(null)).toBe('');
  });

  it('uses the dealership fallback only when the factory field is blank', () => {
    expect(publicManufacturerLine('', 'Texas Home Outlet')).toBe('Texas Home Outlet');
    expect(publicManufacturerLine('Unknown', 'Texas Home Outlet')).toBe('');
    expect(publicManufacturerLine('TRU Homes', 'Texas Home Outlet')).toBe('TRU Homes');
  });
});
