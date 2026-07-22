import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InventoryBrowse, { INVENTORY_PAGE_SIZE } from '../pages/InventoryBrowse';

const homes = Array.from({ length: 40 }, (_, index) => ({
  id: `home-${index + 1}`,
  model_name: `Revenue Model ${index + 1}`,
  manufacturer: 'Texas Test Homes',
  status: 'Orderable',
  inventory_kind: 'orderable_floorplan',
  is_orderable: true,
  display_price: 'Call for Price',
  specs: { beds: 3, baths: 2, sq_ft: 1400 + index },
  image_url: '',
  real_photos: [],
  gallery_images: [],
}));

describe('InventoryBrowse progressive catalog rendering', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ homes }),
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders a bounded first batch and exposes the remaining catalog', async () => {
    const { container } = render(<InventoryBrowse />);

    await waitFor(() => {
      expect(container.querySelectorAll('h3')).toHaveLength(INVENTORY_PAGE_SIZE);
    });
    expect(screen.getByText(`Showing ${INVENTORY_PAGE_SIZE} of 40 homes`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: `Show ${INVENTORY_PAGE_SIZE} more homes` }));

    expect(container.querySelectorAll('h3')).toHaveLength(INVENTORY_PAGE_SIZE * 2);
    expect(screen.getByText(`Showing ${INVENTORY_PAGE_SIZE * 2} of 40 homes`)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show 4 more homes' })).toBeInTheDocument();
  });

  it('searches the complete catalog, including homes outside the first batch', async () => {
    const { container } = render(<InventoryBrowse />);

    await waitFor(() => {
      expect(container.querySelectorAll('h3')).toHaveLength(INVENTORY_PAGE_SIZE);
    });

    fireEvent.change(
      screen.getByRole('textbox', { name: 'Search homes' }),
      { target: { value: 'Revenue Model 40' } },
    );

    await waitFor(() => {
      expect(container.querySelectorAll('h3')).toHaveLength(1);
    });
    expect(screen.getByRole('heading', { name: 'Revenue Model 40', level: 3 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /more homes/i })).not.toBeInTheDocument();
  });
});
