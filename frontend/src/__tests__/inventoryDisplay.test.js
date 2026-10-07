import { describe, expect, it } from 'vitest';
import {
  friendlyModelName,
  isActiveHome,
  possibleDuplicateIds,
  staffVisibleHomes,
  statusLabel,
  stockLabel,
} from '../utils/inventoryDisplay';

describe('staff inventory display', () => {
  it('shows the model, not the website series prefix', () => {
    expect(friendlyModelName('PRE-OWNED / Big Blue')).toBe('Big Blue');
    expect(friendlyModelName('The Promotional Series / The Nassau FAC28483A')).toBe('The Nassau');
    expect(friendlyModelName('Heritage 1684-32A')).toBe('Heritage 1684-32A');
    expect(friendlyModelName('')).toBe('Unnamed home');
  });

  it('prefers stock number, then serial', () => {
    expect(stockLabel({ stock_number: '44490' })).toBe('Stock #44490');
    expect(stockLabel({ serial_number: 'TXL111' })).toBe('Serial TXL111');
    expect(stockLabel({})).toBe('');
  });

  it('treats sold and retired homes as inactive', () => {
    expect(isActiveHome({ status: 'AVAILABLE' })).toBe(true);
    expect(isActiveHome({ status: 'pending' })).toBe(true);
    expect(isActiveHome({ status: 'SOLD' })).toBe(false);
    expect(isActiveHome({ status: 'RETIRED' })).toBe(false);
    expect(statusLabel('RETIRED')).toBe('Removed from website');
  });

  it('shows a duplicated home once and keeps the hint ids', () => {
    const homes = [
      { id: 'fs-big-blue', model_name: 'Big Blue', status: 'AVAILABLE', possible_duplicate_ids: ['44490'] },
      { id: '44490', model_name: 'PRE-OWNED / Big Blue', status: 'AVAILABLE', duplicate_of: 'fs-big-blue' },
      { id: 'sold', model_name: 'Old Home', status: 'SOLD' },
    ];
    expect(staffVisibleHomes(homes).map((h) => h.id)).toEqual(['fs-big-blue']);
    expect(staffVisibleHomes(homes, { includeInactive: true }).map((h) => h.id)).toEqual([
      'fs-big-blue',
      'sold',
    ]);
    expect(possibleDuplicateIds(homes[0])).toEqual(['44490']);
  });
});
