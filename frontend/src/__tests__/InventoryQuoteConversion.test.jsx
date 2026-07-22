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

const orderableHome = {
  ...home,
  id: 'floorplan-236866',
  model_name: 'Champion Sycamore',
  status: 'Orderable',
  inventory_kind: 'orderable_floorplan',
  is_orderable: true,
  is_in_stock: false,
  quote_url: '/quote/floorplan/236866/dealer/3522/',
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

  it('routes an orderable floorplan build-options CTA through the attributed quote flow', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ homes: [orderableHome] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ success: true, lead_id: 'lead-orderable-1' }),
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<InventoryBrowse />);
    fireEvent.click(await screen.findByRole('button', { name: 'View Details' }));

    expect(screen.queryByRole('button', { name: 'Schedule a Tour' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discuss Build Options' }));

    expect(screen.getByText('Get a Price Quote')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Your full name'), {
      target: { value: 'Jordan Buyer' },
    });
    fireEvent.change(screen.getByPlaceholderText('(281) 000-0000'), {
      target: { value: '(281) 555-0199' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Get Quote' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [contactUrl, contactOptions] = fetchMock.mock.calls[1];
    const payload = JSON.parse(contactOptions.body);
    expect(contactUrl).toBe('/api/contact');
    expect(payload).toEqual(expect.objectContaining({
      source: 'inventory_quote',
      home_id: 'floorplan-236866',
      home_model: 'Champion Sycamore',
    }));
    expect(analyticsTrackEvent).toHaveBeenCalledWith(
      'lead_form_opened',
      expect.objectContaining({ home_id: 'floorplan-236866', type: 'price' }),
    );
  });

  it('preserves the tour flow for a listed home', async () => {
    render(<InventoryBrowse />);
    fireEvent.click(await screen.findByRole('button', { name: 'View Details' }));

    expect(screen.queryByRole('button', { name: 'Discuss Build Options' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Schedule a Tour' }));

    expect(screen.getByRole('heading', { name: 'Schedule a Tour' })).toBeInTheDocument();
    expect(analyticsTrackEvent).toHaveBeenCalledWith(
      'lead_form_opened',
      expect.objectContaining({ home_id: '43372', type: 'tour' }),
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
