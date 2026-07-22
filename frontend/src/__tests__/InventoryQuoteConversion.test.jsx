import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';

const { analyticsTrackEvent } = vi.hoisted(() => ({ analyticsTrackEvent: vi.fn() }));
vi.mock('../utils/analytics', () => ({ trackEvent: analyticsTrackEvent }));

const home = {
  id: '43372',
  model_name: 'Premier / Creole 3256H32447',
  manufacturer: 'Champion Homes',
  status: 'Available',
  inventory_kind: 'available_now',
  display_price: 'Call for Price',
  quote_url: '/quote/inventory/43372/dealer/3522/',
  image_url: 'https://cdn.example.com/creole.jpg',
  real_photos: ['https://cdn.example.com/creole.jpg'],
  specs: { beds: 3, baths: 2, sq_ft: 1699 },
};

describe('InventoryBrowse quote conversion path', () => {
  beforeEach(() => {
    analyticsTrackEvent.mockClear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ homes: [home] }),
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('opens the quote form from the featured CTA without leaving the storefront', async () => {
    const { container } = render(<InventoryBrowse />);
    const [featuredQuote] = await screen.findAllByRole(
      'button',
      { name: 'Check Price & Availability' },
    );

    expect(container.querySelector('a[href^="/quote/"]')).toBeNull();
    fireEvent.click(featuredQuote);

    await waitFor(() => {
      expect(container.querySelector('form')).toBeInTheDocument();
    });
    expect(screen.getByText('Get a Price Quote')).toBeInTheDocument();
  });

  it('keeps quote intent inside the detail modal too', async () => {
    const { container } = render(<InventoryBrowse />);
    const details = await screen.findByRole('button', { name: 'View Details' });
    fireEvent.click(details);

    const availabilityButtons = screen.getAllByRole(
      'button',
      { name: 'Check Price & Availability' },
    );
    fireEvent.click(availabilityButtons.at(-1));

    await waitFor(() => {
      expect(container.querySelector('form')).toBeInTheDocument();
    });
    expect(container.querySelector('a[href^="/quote/"]')).toBeNull();
    expect(analyticsTrackEvent).toHaveBeenCalledWith(
      'home_viewed',
      expect.objectContaining({ home_id: '43372' }),
    );
    expect(analyticsTrackEvent).toHaveBeenCalledWith(
      'lead_form_opened',
      expect.objectContaining({ home_id: '43372' }),
    );
  });

  it('never describes the inventory snapshot as live or available now', async () => {
    render(<InventoryBrowse />);

    expect(await screen.findByText('Inventory changes daily')).toBeInTheDocument();
    expect(screen.queryByText(/Live Inventory/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Featured Live Listing/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Available now/i)).not.toBeInTheDocument();
  });
});
