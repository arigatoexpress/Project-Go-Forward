import { describe, it, expect } from 'vitest';
import {
  insuranceEscrowMonthly,
  taxEscrowMonthly,
} from '../pages/DocumentCenter';

// Mirrors the backend math in tools/document_engine.py::_compute_fields.
// Spec (Mark Willcott, 2026-06-24):
//   monthly INS escrow = annual_insurance / 12
//   monthly tax escrow = ((tax_rate/100) * taxable_value) / 12
//   taxable_value defaults to sales_price when blank.

describe('insuranceEscrowMonthly', () => {
  it('divides the annual premium by 12', () => {
    expect(insuranceEscrowMonthly({ annual_insurance: '1200' })).toBe('100.00');
  });

  it('rounds to two decimals', () => {
    expect(insuranceEscrowMonthly({ annual_insurance: '1000' })).toBe('83.33');
  });

  it('accepts a currency-formatted string', () => {
    expect(insuranceEscrowMonthly({ annual_insurance: '$1,440.00' })).toBe('120.00');
  });

  it('treats zero premium as 0.00', () => {
    expect(insuranceEscrowMonthly({ annual_insurance: '0' })).toBe('0.00');
  });

  it('returns empty string when no premium entered', () => {
    expect(insuranceEscrowMonthly({})).toBe('');
    expect(insuranceEscrowMonthly({ annual_insurance: '' })).toBe('');
  });
});

describe('taxEscrowMonthly', () => {
  it('uses an explicit taxable value', () => {
    expect(taxEscrowMonthly({ tax_rate: '2.5', taxable_value: '100000' })).toBe('208.33');
  });

  it('defaults the taxable value to the sales price', () => {
    expect(taxEscrowMonthly({ tax_rate: '1.8', sales_price: '120000' })).toBe('180.00');
  });

  it('prefers the explicit taxable value over the sales price', () => {
    expect(
      taxEscrowMonthly({ tax_rate: '2.0', taxable_value: '80000', sales_price: '200000' }),
    ).toBe('133.33');
  });

  it('accepts currency / percent strings', () => {
    expect(taxEscrowMonthly({ tax_rate: '2.0', taxable_value: '$90,000' })).toBe('150.00');
  });

  it('treats a zero rate as 0.00', () => {
    expect(taxEscrowMonthly({ tax_rate: '0', sales_price: '100000' })).toBe('0.00');
  });

  it('returns empty string when no rate is entered', () => {
    expect(taxEscrowMonthly({ sales_price: '100000' })).toBe('');
  });

  it('returns empty string when a rate is set but no value is available', () => {
    expect(taxEscrowMonthly({ tax_rate: '2.0' })).toBe('');
  });
});
