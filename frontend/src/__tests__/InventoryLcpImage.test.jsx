import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';

const homes = [
  {
    id: '28102',
    model_name: 'Tru / Dogwood',
    manufacturer: 'Tru',
    status: 'Available',
    inventory_kind: 'available_now',
    display_price: 'Call for Price',
    specs: { beds: 3, baths: 2, sq_ft: 1200 },
    image_url: '/api/inventory/photos/28102/hero.jpg',
    real_photos: ['/api/inventory/photos/28102/hero.jpg'],
  },
];

describe('inventory hero when the server already painted LCP', () => {
  beforeEach(() => {
    window.localStorage.clear();
    const painted = document.createElement('img');
    painted.id = 'tho-lcp';
    painted.alt = '';
    document.body.appendChild(painted);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, homes }),
    }));
  });

  afterEach(() => {
    cleanup();
    document.getElementById('tho-lcp')?.remove();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/inventory');
  });

  it('keeps the static image and does not start a second high-priority hero', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse pathname="/inventory" />);

    expect(await screen.findByRole('heading', { name: /Home Inventory in / })).toBeTruthy();
    expect(document.getElementById('tho-lcp')).not.toBeNull();
    const competing = [...document.querySelectorAll('img[fetchpriority="high"]')]
      .filter((img) => img.id !== 'tho-lcp');
    expect(competing).toHaveLength(0);
    const spotlight = document.querySelector('img[src="/api/inventory/photos/28102/hero.jpg"]');
    expect(spotlight).not.toBeNull();
    expect(spotlight.getAttribute('fetchpriority')).toBe('low');
    expect(spotlight.getAttribute('loading')).toBe('lazy');
  });
});
