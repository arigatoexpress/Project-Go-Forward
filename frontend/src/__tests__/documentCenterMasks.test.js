import { describe, it, expect } from 'vitest';
import {
  formatPhoneMask,
  formatSsnMask,
  joinLengthUnit,
} from '../pages/DocumentCenter';

describe('formatPhoneMask (item A1 — 111-222-3333)', () => {
  it('formats a 10-digit number with dashes after the 3rd and 6th digit', () => {
    expect(formatPhoneMask('2813243020')).toBe('281-324-3020');
  });

  it('formats progressively as the user types', () => {
    expect(formatPhoneMask('2')).toBe('2');
    expect(formatPhoneMask('281')).toBe('281');
    expect(formatPhoneMask('2813')).toBe('281-3');
    expect(formatPhoneMask('281324')).toBe('281-324');
    expect(formatPhoneMask('2813243')).toBe('281-324-3');
  });

  it('strips existing formatting and re-applies the mask', () => {
    expect(formatPhoneMask('(281) 324-3020')).toBe('281-324-3020');
    expect(formatPhoneMask('281.324.3020')).toBe('281-324-3020');
  });

  it('caps at 10 digits (drops overflow)', () => {
    expect(formatPhoneMask('281324302099')).toBe('281-324-3020');
  });

  it('leaves an international +1 value untouched', () => {
    expect(formatPhoneMask('+12813243020')).toBe('+12813243020');
  });

  it('handles empty / null / undefined safely', () => {
    expect(formatPhoneMask('')).toBe('');
    expect(formatPhoneMask(null)).toBe('');
    expect(formatPhoneMask(undefined)).toBe('');
  });
});

describe('formatSsnMask (item A3 — 111-22-3333)', () => {
  it('formats a 9-digit SSN with dashes after the 3rd and 5th digit', () => {
    expect(formatSsnMask('123456789')).toBe('123-45-6789');
  });

  it('formats progressively as the user types', () => {
    expect(formatSsnMask('12')).toBe('12');
    expect(formatSsnMask('123')).toBe('123');
    expect(formatSsnMask('1234')).toBe('123-4');
    expect(formatSsnMask('12345')).toBe('123-45');
    expect(formatSsnMask('123456')).toBe('123-45-6');
  });

  it('caps at 9 digits', () => {
    expect(formatSsnMask('1234567890')).toBe('123-45-6789');
  });

  it('strips existing formatting', () => {
    expect(formatSsnMask('123 45 6789')).toBe('123-45-6789');
  });

  it('handles empty / null / undefined safely', () => {
    expect(formatSsnMask('')).toBe('');
    expect(formatSsnMask(null)).toBe('');
    expect(formatSsnMask(undefined)).toBe('');
  });
});

describe('joinLengthUnit (item A5 — numeric value + unit)', () => {
  it('combines a number with a unit', () => {
    expect(joinLengthUnit('5', 'Years')).toBe('5 Years');
    expect(joinLengthUnit('18', 'Months')).toBe('18 Months');
  });

  it('defaults to Years when the unit is blank', () => {
    expect(joinLengthUnit('5', '')).toBe('5 Years');
    expect(joinLengthUnit('5', undefined)).toBe('5 Years');
  });

  it('returns empty string when there is no number (never a bare unit)', () => {
    expect(joinLengthUnit('', 'Years')).toBe('');
    expect(joinLengthUnit(undefined, 'Months')).toBe('');
    expect(joinLengthUnit(null, 'Years')).toBe('');
  });
});
