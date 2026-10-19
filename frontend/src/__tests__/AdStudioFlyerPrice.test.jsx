import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import adminFetch from '../adminFetch';
import AdStudio from '../pages/AdStudio';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

const scriptPayload = {
  success: true,
  script_id: 'SCR-1',
  platform: 'tiktok',
  hashtags: ['#manufacturedhomes'],
  scripts: [
    {
      hook: 'Look at this home',
      body: 'Three bedrooms on a quiet lot.',
      cta: 'Come see it at Texas Home Outlet.',
      duration_estimate: '20s',
    },
  ],
};

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: vi.fn().mockResolvedValue(payload),
  };
}

const inventoryState = { homes: [] };

function installFetch() {
  adminFetch.mockImplementation((url) => {
    if (url === '/api/marketing/voiceover-voices') {
      return Promise.resolve(jsonResponse({ success: true, voices: [] }));
    }
    if (url === '/api/marketing/gcp-readiness') {
      return Promise.resolve(jsonResponse({ success: true, ready: false }));
    }
    if (url === '/api/marketing/inventory-context') {
      return Promise.resolve(jsonResponse({ success: true, homes: inventoryState.homes }));
    }
    if (url === '/api/marketing/generate-script') {
      return Promise.resolve(jsonResponse(scriptPayload));
    }
    if (url === '/api/marketing/generate-flyer') {
      return Promise.resolve(jsonResponse({ success: true, image_base64: 'abc', filename: 'flyer.png' }));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

function flyerBodies() {
  return adminFetch.mock.calls
    .filter((call) => call[0] === '/api/marketing/generate-flyer')
    .map((call) => JSON.parse(call[1].body));
}

describe('Ad Studio flyer price', () => {
  beforeEach(() => {
    inventoryState.homes = [];
    adminFetch.mockReset();
    installFetch();
  });

  it('sends an explicit home price and omits a blank one', async () => {
    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Home model name/), {
      target: { value: 'The Delight' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));
    expect(await screen.findByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Make a flyer to download/ }));
    await waitFor(() => expect(flyerBodies()).toHaveLength(1));
    expect(flyerBodies()[0].home_name).toBe('The Delight');
    expect(flyerBodies()[0].home_price).toBeUndefined();

    fireEvent.change(screen.getByPlaceholderText(/Call for Price/), {
      target: { value: '$89,900' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Make a flyer to download/ }));
    await waitFor(() => expect(flyerBodies()).toHaveLength(2));
    expect(flyerBodies()[1].home_price).toBe('$89,900');
  });

  it('leaves the price field empty when a home is picked', async () => {
    inventoryState.homes = [{
      id: 'delight',
      model_name: 'The Delight',
      manufacturer: 'Example Homes',
      status: 'Available',
      display_price: '$89,900',
      specs: { beds: 3, baths: 2, sq_ft: 1280 },
      real_photos: [],
    }];

    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Choose a home' }));
    fireEvent.click(await screen.findByRole('button', { name: /The Delight/ }));

    const priceField = screen.getByPlaceholderText(/Call for Price/);
    expect(priceField).toHaveValue('');
    expect(screen.getByPlaceholderText(/Home model name/)).toHaveValue('The Delight');

    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));
    expect(await screen.findByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Make a flyer to download/ }));
    await waitFor(() => expect(flyerBodies()).toHaveLength(1));
    expect(flyerBodies()[0].home_name).toBe('The Delight');
    expect(flyerBodies()[0].home_price).toBeUndefined();

    fireEvent.change(priceField, { target: { value: '$89,900' } });
    fireEvent.click(screen.getByRole('button', { name: /Make a flyer to download/ }));
    await waitFor(() => expect(flyerBodies()).toHaveLength(2));
    expect(flyerBodies()[1].home_price).toBe('$89,900');
  });
});
