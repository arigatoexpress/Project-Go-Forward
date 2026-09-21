import { afterEach, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';
import PhotoManager from '../pages/PhotoManager';
import AdStudio from '../pages/AdStudio';

const endpoint = '/api/marketing/inventory-context';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

it.each([
  { name: 'customer inventory', Component: InventoryBrowse },
  { name: 'photo home picker', Component: PhotoManager },
  { name: 'Ad Studio inventory picker', Component: AdStudio, openPicker: true },
])('$name revalidates an old cached catalog on first navigation', async ({ Component, openPicker }) => {
  // Model a browser holding the pre-cutover response with max-age=3600:
  // only a request that requires revalidation receives the current listing.
  const fetchMock = vi.fn(async (url, options = {}) => {
    if (url === endpoint) {
      const model = options.cache === 'no-cache' ? 'Updated staff listing' : 'Retired cached listing';
      return { ok: true, status: 200, json: async () => ({ success: true, homes: [{
        id: 'listing', model_name: model, manufacturer: 'Example',
        status: 'Available', classification: 'Single Wide', inventory_kind: 'available_now',
        specs: { beds: 3, baths: 2, sq_ft: 1200 }, display_price: 'Call for Price',
        real_photos: [], gallery_images: [], features: [],
      }] }) };
    }
    return { ok: true, status: 200, json: async () => ({ success: true, voices: [], ready: false }) };
  });
  vi.stubGlobal('fetch', fetchMock);

  render(createElement(Component));
  if (openPicker) fireEvent.click(screen.getByRole('button', { name: 'Browse Inventory' }));

  expect((await screen.findAllByText(/Updated staff listing/)).length).toBeGreaterThan(0);
  expect(screen.queryByText(/Retired cached listing/)).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(endpoint, expect.objectContaining({ cache: 'no-cache' }));
});
