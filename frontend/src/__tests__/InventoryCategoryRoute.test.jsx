import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';
import {
  getInventoryCategoryRoute,
  isInventoryCategoryPath,
  normalizeInventoryClassification,
} from '../utils/inventoryCategoryRoutes';

const homes = [
  {
    id: 'single-1',
    model_name: 'The Narrow Star',
    manufacturer: 'Texas Test Homes',
    classification: 'Single Wide',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    display_price: 'Call for Price',
    specs: { beds: 3, baths: 2, sq_ft: 1200 },
    image_url: '',
    real_photos: [],
    gallery_images: [],
  },
  {
    id: 'double-1',
    model_name: 'The Wide Horizon',
    manufacturer: 'Texas Test Homes',
    classification: 'Double Wide',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    display_price: 'Call for Price',
    specs: { beds: 4, baths: 2, sq_ft: 1900 },
    image_url: '',
    real_photos: [],
    gallery_images: [],
  },
];

describe('indexable inventory category routes', () => {
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
    window.history.replaceState({}, '', '/');
  });

  it('recognizes only the two canonical category paths and normalizes a trailing slash', () => {
    expect(getInventoryCategoryRoute('/single-wide/')).toMatchObject({
      path: '/single-wide',
      classification: 'Single Wide',
    });
    expect(getInventoryCategoryRoute('/DOUBLE-WIDE')).toMatchObject({
      path: '/double-wide',
      classification: 'Double Wide',
    });
    expect(isInventoryCategoryPath('/single-wide')).toBe(true);
    expect(isInventoryCategoryPath('/inventory')).toBe(false);
    expect(getInventoryCategoryRoute('/used-homes')).toBeNull();
    expect(normalizeInventoryClassification('  SINGLE_section ')).toBe('Single Wide');
    expect(normalizeInventoryClassification('double-wide')).toBe('Double Wide');
    expect(normalizeInventoryClassification(' multi section ')).toBe('Double Wide');
  });

  it('hydrates a single-wide landing page with matching copy and homes only', async () => {
    window.history.replaceState({}, '', '/single-wide');
    render(<InventoryBrowse />);

    await waitFor(() => {
      expect(screen.getByRole('heading', {
        name: 'Single Wide Manufactured Homes in Huffman, TX',
        level: 1,
      })).toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { name: 'The Narrow Star', level: 3 })).toBeInTheDocument();
    expect(screen.queryByText('The Wide Horizon')).not.toBeInTheDocument();
    expect(screen.getByText('1 home')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    expect(screen.getByRole('combobox', { name: 'Home type' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Clear All' })).not.toBeInTheDocument();
  });

  it('uses only double-wide homes for the double-wide hero and catalog', async () => {
    window.history.replaceState({}, '', '/double-wide');
    render(<InventoryBrowse />);

    await waitFor(() => {
      expect(screen.getByRole('heading', {
        name: 'Double Wide Manufactured Homes in Huffman, TX',
        level: 1,
      })).toBeInTheDocument();
    });
    expect(screen.getAllByText('The Wide Horizon').length).toBeGreaterThan(0);
    expect(screen.queryByText('The Narrow Star')).not.toBeInTheDocument();
  });

  it('links the generic inventory hub to both crawlable category pages', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse />);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Single Wide Homes' })).toHaveAttribute(
        'href',
        '/single-wide',
      );
    });
    expect(screen.getByRole('link', { name: 'Double Wide Homes' })).toHaveAttribute(
      'href',
      '/double-wide',
    );
  });

  it('shows recovery links instead of ineffective filters for a genuinely empty category', async () => {
    window.history.replaceState({}, '', '/single-wide');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ homes: [homes[1]] }),
    }));
    render(<InventoryBrowse />);

    expect(await screen.findByText('No Single Wide homes are listed right now')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Browse All Homes' })).toHaveAttribute(
      'href',
      '/inventory',
    );
    expect(screen.getByRole('link', { name: 'Contact Us' })).toHaveAttribute('href', '/contact');
    expect(screen.queryByRole('button', { name: /clear all/i })).not.toBeInTheDocument();
  });

  it('shows retry and call recovery when the API reports a category outage', async () => {
    window.history.replaceState({}, '', '/single-wide');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: false,
        error: 'Failed to load inventory context. Please try again.',
      }),
    }));
    render(<InventoryBrowse />);

    expect(await screen.findByText('Unable to load inventory')).toBeInTheDocument();
    expect(screen.getByText('Failed to load inventory context. Please try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /call us at/i }).getAttribute('href')).toMatch(/^tel:\+\d+$/);
    expect(screen.queryByText('No Single Wide homes are listed right now')).not.toBeInTheDocument();
  });
});
