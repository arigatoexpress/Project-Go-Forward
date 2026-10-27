import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';

const homes = [
  {
    id: 'with-tour',
    model_name: 'Nexus / Leo 9156',
    manufacturer: 'Nexus',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    is_orderable: true,
    display_price: 'Call for Price',
    specs: { beds: 3, baths: 2, sq_ft: 1500 },
    image_url: '',
    real_photos: [],
    floorplan_url: 'https://cdn.example/leo-plan.jpg',
    matterport_id: 'cDQrpPT5waQ',
  },
  {
    id: 'no-tour',
    model_name: 'Compass / Ocampo 2306P',
    manufacturer: 'Compass',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    is_orderable: true,
    display_price: 'Call for Price',
    specs: { beds: 3, baths: 2, sq_ft: 1400 },
    image_url: '',
    real_photos: [],
    floorplan_url: 'https://cdn.example/ocampo-plan.png',
  },
];

describe('inventory 3D tour filter and card button', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, homes }),
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/inventory');
  });

  it('filters the grid to homes with a Matterport tour', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse pathname="/inventory" />);

    expect(await screen.findAllByRole('heading', { name: 'Nexus / Leo 9156' })).not.toHaveLength(0);
    expect(screen.getAllByRole('heading', { name: 'Compass / Ocampo 2306P' })).toHaveLength(1);

    const toggle = screen.getByRole('button', { name: /^3D tour \(/i });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('heading', { name: 'Nexus / Leo 9156' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('heading', { name: 'Compass / Ocampo 2306P' })).toBeNull();
  });

  it('opens the existing in-modal tour from the card button', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse pathname="/inventory" />);

    const watch = await screen.findByRole('button', { name: /watch 3d tour/i });
    fireEvent.click(watch);

    const frame = await screen.findByTitle('Nexus / Leo 9156 3D Tour');
    expect(frame).toHaveAttribute('src', 'https://my.matterport.com/show/?m=cDQrpPT5waQ&play=1');
    const tourLink = screen.getByRole('link', { name: /open 3d tour for nexus/i });
    expect(tourLink).toHaveAttribute('href', 'https://my.matterport.com/show/?m=cDQrpPT5waQ&play=1');
    expect(screen.getAllByRole('button', { name: /watch 3d tour/i }).length).toBeGreaterThan(0);
  });

  it('shows each floor plan on its card before the tour filter', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse pathname="/inventory" />);
    await waitFor(() => {
      expect(document.querySelector('img[src="https://cdn.example/leo-plan.jpg"]')).not.toBeNull();
      expect(document.querySelector('img[src="https://cdn.example/ocampo-plan.png"]')).not.toBeNull();
    });
  });
});
