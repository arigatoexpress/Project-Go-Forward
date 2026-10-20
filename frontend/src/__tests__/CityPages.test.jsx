import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ToastProvider } from '../components/Toast';
import App from '../App';
import InventoryBrowse from '../pages/InventoryBrowse';
import Locations from '../pages/Locations';
import {
  BUSINESS_CITY,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_NAME,
  BUSINESS_PHONE,
  SERVICE_AREA_CITIES,
} from '../constants';

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

describe('city pages and the locations index', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.scrollTo = vi.fn();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }));
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url) === '/api/marketing/inventory-context') {
        return response({
          success: true,
          homes: [
            {
              id: '44490',
              stock_number: '44490',
              model_name: 'PRE-OWNED / Big Blue',
              status: 'Pre-Owned',
              inventory_kind: 'pre_owned',
              display_price: 'Call for Price',
              specs: { beds: 3, baths: 2 },
            },
          ],
        });
      }
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  it('shows Humble local facts, the held delivery sentence, and the locations breadcrumb', async () => {
    window.history.replaceState({}, '', '/manufactured-homes-in-humble-tx');
    render(<InventoryBrowse pathname="/manufactured-homes-in-humble-tx" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Manufactured & Mobile Homes in Humble, TX' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Humble beside U.S. Highway 59' })).toBeInTheDocument();
    expect(screen.getByText(/northern Harris County/)).toBeInTheDocument();
    const held = `${BUSINESS_NAME} delivers manufactured and mobile homes to Humble, TX from the ${BUSINESS_CITY} showroom at ${BUSINESS_FULL_ADDRESS}. Call ${BUSINESS_PHONE} for the current price.`;
    expect(screen.getByText(held)).toBeInTheDocument();
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(crumbs).toHaveTextContent('Home');
    expect(crumbs).toHaveTextContent('Locations');
    expect(crumbs).toHaveTextContent('Homes in Humble, TX');
    expect(crumbs.querySelector('a[href="/locations"]')).not.toBeNull();
    expect(screen.getByRole('link', { name: 'All homes' })).toHaveAttribute('href', '/inventory');
    expect(await screen.findByRole('link', { name: 'PRE-OWNED / Big Blue' })).toHaveAttribute('href', '/homes/44490-pre-owned-big-blue');
  });

  it('links every served town from the locations page', async () => {
    render(<Locations />);
    expect(screen.getByRole('heading', { level: 1, name: 'Locations' })).toBeInTheDocument();
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(crumbs).toHaveTextContent('Home');
    expect(crumbs).toHaveTextContent('Locations');
    for (const city of SERVICE_AREA_CITIES) {
      const slug = city.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
      const links = screen.getAllByRole('link', { name: `Homes in ${city}, TX` });
      expect(links.some((link) => link.getAttribute('href') === `/manufactured-homes-in-${slug}-tx`)).toBe(true);
    }
    expect(screen.getByRole('link', { name: 'Home inventory' })).toHaveAttribute('href', '/inventory');
    expect(screen.getByRole('link', { name: 'Showroom contact page' })).toHaveAttribute('href', '/contact');
  });

  it('routes /locations through the app shell', async () => {
    window.history.replaceState({}, '', '/locations');
    render(<ToastProvider><App /></ToastProvider>);
    expect(await screen.findByRole('heading', { level: 1, name: 'Locations' })).toBeInTheDocument();
    expect(document.title).toBe('Locations | Texas Home Outlet');
  });
});
