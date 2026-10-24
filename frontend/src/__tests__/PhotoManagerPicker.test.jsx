import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PhotoManager from '../pages/PhotoManager';
import adminFetch from '../adminFetch';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));

function jsonResponse(body) {
  return { ok: true, status: 200, json: vi.fn().mockResolvedValue(body) };
}

const homes = [
  { id: '44490', model_name: 'PRE-OWNED / Big Blue', status: 'AVAILABLE', stock_number: '44490', real_photos: [] },
  { id: 'dup', model_name: 'Big Blue', status: 'AVAILABLE', duplicate_of: '44490', real_photos: [] },
  { id: 'sold', model_name: 'Old Sold Home', status: 'SOLD', stock_number: '100', real_photos: [] },
  { id: 'series', model_name: 'The Promotional Series / The Nassau FAC28483A', status: 'AVAILABLE', serial_number: 'TXL9', real_photos: ['a.jpg'] },
];

const orderable = {
  id: 'floorplan-223034',
  model_name: 'Skyliner / 4732B',
  inventory_kind: 'orderable_floorplan',
  is_orderable: true,
  status: 'Orderable',
  real_photos: [],
  floorplan_url: 'https://cdn.example/plan/floor-plans.jpg',
  floorplan_urls: ['https://cdn.example/plan/floor-plans.jpg'],
};

function homeRadios() {
  return within(screen.getByRole('radiogroup', { name: /Choose a home/i })).getAllByRole('radio');
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('Image', class {
    set src(_value) {
      queueMicrotask(() => this.onload?.());
    }
  });
  adminFetch.mockImplementation((url) => {
    if (String(url).includes('/api/marketing/inventory-context')) {
      return Promise.resolve(jsonResponse({ success: true, homes: [orderable] }));
    }
    if (String(url).startsWith('/api/inventory?')) {
      expect(String(url)).toContain('include_staff_photos=true');
      return Promise.resolve(jsonResponse({ success: true, inventory: homes }));
    }
    return Promise.resolve(jsonResponse({ photos: [] }));
  });
});

describe('Photo Manager home picker', () => {
  it('opens on homes that need photos, including orderable plans with only a floor plan', async () => {
    render(<PhotoManager onBack={vi.fn()} />);

    expect(await screen.findByText('Big Blue')).toBeInTheDocument();
    expect(screen.getByText('Stock #44490 · No photo yet')).toBeInTheDocument();
    expect(screen.getByText('Skyliner 4732B')).toBeInTheDocument();
    expect(screen.getByText('Orderable plan · Only a floor plan')).toBeInTheDocument();
    expect(screen.getByText(/These homes show a floor plan, or their picture did not load/)).toBeInTheDocument();
    expect(screen.queryByText('The Nassau')).toBeNull();
    expect(screen.queryByText(/PRE-OWNED/)).toBeNull();
    expect(screen.queryByText(/Promotional Series/)).toBeNull();
    expect(screen.queryByText('Old Sold Home')).toBeNull();
    expect(homeRadios()).toHaveLength(2);
    expect(screen.getByRole('radiogroup', { name: /Choose a home/i }).className).toMatch(/break-words|overflow/);
    expect(screen.getByRole('radio', { name: /Needs photos/ })).toHaveAttribute('aria-checked', 'true');
  });

  it('shows homes that already have a photo from All homes', async () => {
    render(<PhotoManager onBack={vi.fn()} />);
    await screen.findByText('Big Blue');

    fireEvent.click(screen.getByRole('radio', { name: 'All homes' }));
    expect(screen.getByText('The Nassau')).toBeInTheDocument();
    expect(screen.getByText('Serial TXL9')).toBeInTheDocument();
    expect(screen.getByText('Pick a home, then add a photo you took.')).toBeInTheDocument();
  });

  it('adds a home to Needs photos when its picture does not load', async () => {
    vi.stubGlobal('Image', class {
      set src(value) {
        queueMicrotask(() => {
          if (String(value).includes('dead')) this.onerror?.();
          else this.onload?.();
        });
      }
    });
    adminFetch.mockImplementation((url) => {
      if (String(url).includes('/api/marketing/inventory-context')) {
        return Promise.resolve(jsonResponse({
          success: true,
          homes: [
            {
              id: 'dead-home',
              model_name: 'Hidden Photo',
              status: 'AVAILABLE',
              real_photos: ['https://cdn.example/dead.jpg'],
            },
            {
              id: 'live-home',
              model_name: 'Visible Photo',
              status: 'AVAILABLE',
              real_photos: ['https://cdn.example/live.jpg'],
            },
          ],
        }));
      }
      if (String(url).startsWith('/api/inventory?')) {
        return Promise.resolve(jsonResponse({ success: true, inventory: [] }));
      }
      return Promise.resolve(jsonResponse({ photos: [] }));
    });

    render(<PhotoManager onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Hidden Photo')).toBeInTheDocument();
      expect(screen.queryByText('Checking a few website pictures…')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Picture did not load')).toBeInTheDocument();
    expect(screen.queryByText('Visible Photo')).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: /Hidden Photo/i }));
    expect(screen.getByText(/The picture on the website did not load/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument());
  });

  it('shows sold homes only after staff tick Include sold/archived', async () => {
    render(<PhotoManager onBack={vi.fn()} />);
    await screen.findByText('Big Blue');

    fireEvent.click(screen.getByLabelText('Include sold/archived homes'));
    expect(screen.getByText('Old Sold Home')).toBeInTheDocument();
    expect(screen.getByText(/Sold · No photo yet/)).toBeInTheDocument();
  });
});
