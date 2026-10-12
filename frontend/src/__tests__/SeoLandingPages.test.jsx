import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';

const homes = [
  {
    id: '44490',
    model_name: 'PRE-OWNED / Big Blue',
    manufacturer: 'Pre-Owned',
    status: 'Pre-Owned',
    inventory_kind: 'pre_owned',
    display_price: 'Call for Price',
    image_url: 'https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg',
    real_photos: [
      'https://lot.example/44490.jpg',
      'https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg',
    ],
    gallery_images: [
      'https://cdn.example/catalog.jpg',
      'https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg',
    ],
    floor_plan_url: 'https://lot.example/plan.jpg',
    specs: { beds: 3, baths: 2, sq_ft: 1152 },
  },
  {
    id: 'floorplan-223034',
    legacy_plan_id: '223034',
    model_name: 'Skyliner 4732B',
    manufacturer: 'Skyline',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    display_price: 'Call for Price',
    detail_url: '/plan/223034/skyliner/4732b/',
    specs: { beds: 3, baths: 2 },
  },
];

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

describe('unique city, floorplan, and home pages after hydration', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url) === '/api/marketing/inventory-context') return response({ success: true, homes });
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  it('keeps the Humble city title and H1 instead of the generic Huffman inventory title', async () => {
    window.history.replaceState({}, '', '/manufactured-homes-in-humble-tx');
    render(<InventoryBrowse pathname="/manufactured-homes-in-humble-tx" />);
    await waitFor(() => {
      expect(screen.getByRole('heading', {
        level: 1,
        name: 'Manufactured & Mobile Homes in Humble, TX',
      })).toBeInTheDocument();
    });
    expect(document.title).toContain('Humble, TX');
    expect(document.title).not.toContain('Homes for Sale in Huffman, TX');
    expect(document.querySelector('link[rel="canonical"]')?.getAttribute('href')).toContain('/manufactured-homes-in-humble-tx');
    expect(screen.getByText(/delivers new manufactured and mobile homes to Humble, TX/i)).toBeInTheDocument();
  });

  it('keeps the floorplan title and H1 on a plan URL', async () => {
    window.history.replaceState({}, '', '/plan/223034/skyliner/4732b/');
    render(<InventoryBrowse pathname="/plan/223034/skyliner/4732b/" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Skyliner 4732B' })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toContain('Skyliner 4732B'));
    expect(document.title).not.toContain('Homes for Sale in Huffman, TX');
  });

  it('renders an in-stock home page with its own title, price, photos, and card links', async () => {
    window.history.replaceState({}, '', '/homes/44490-pre-owned-big-blue');
    render(<InventoryBrowse pathname="/homes/44490-pre-owned-big-blue" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'PRE-OWNED / Big Blue' })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toContain('PRE-OWNED / Big Blue'));
    expect(screen.getAllByText('Call for Price').length).toBeGreaterThan(0);
    expect(document.querySelector('img[src="https://lot.example/44490.jpg"]')).not.toBeNull();
    expect(document.querySelector('img[src="https://cdn.example/catalog.jpg"]')).toBeNull();
    expect(document.querySelector('img[src="https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg"]')).toBeNull();
    const details = screen.getAllByRole('link', { name: /View Details|View PRE-OWNED/i });
    expect(details.some((link) => link.getAttribute('href') === '/homes/44490-pre-owned-big-blue')).toBe(true);
  });
});
