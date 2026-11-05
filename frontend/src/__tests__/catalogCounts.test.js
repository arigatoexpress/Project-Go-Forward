import { describe, expect, it } from 'vitest';
import { catalogKind, countCatalog, formatCatalogCounts } from '../utils/catalogCounts';

describe('catalog counts', () => {
  it('keeps the API inventory_kind and does not fold pre-owned into on-the-lot', () => {
    expect(catalogKind({ inventory_kind: 'pre_owned', status: 'Available' })).toBe('pre_owned');
    expect(catalogKind({ status: 'Orderable' })).toBe('orderable_floorplan');
    expect(catalogKind({ status: 'Pre-Owned' })).toBe('pre_owned');
    expect(catalogKind({ status: 'Available' })).toBe('available_now');
  });

  it('counts each bucket once and does not invent a mixed houses total', () => {
    const counts = countCatalog([
      { inventory_kind: 'available_now' },
      { inventory_kind: 'available_now', matterport_id: 'abc' },
      { inventory_kind: 'pre_owned' },
      { inventory_kind: 'orderable_floorplan' },
      { inventory_kind: 'orderable_floorplan' },
      { inventory_kind: 'held_back' },
    ]);
    expect(counts).toEqual({
      availableNow: 2,
      preOwned: 1,
      orderable: 2,
      other: 1,
      tours: 1,
    });
    // The old "Houses" tile was available_now + orderable and left pre-owned out.
    expect(counts.availableNow + counts.orderable).toBe(4);
    expect(counts.availableNow + counts.preOwned + counts.orderable + counts.other).toBe(6);
    expect(formatCatalogCounts(counts)).toBe(
      '2 on the lot, 1 pre-owned, 2 orderable floorplans, and 1 other listing.',
    );
    expect(formatCatalogCounts(counts, { scope: 'group' })).toBe(
      'In this group: 2 on the lot, 1 pre-owned, 2 orderable floorplans, and 1 other listing.',
    );
  });
});
