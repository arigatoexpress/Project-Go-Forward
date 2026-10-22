import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';
import Contact from '../pages/Contact';
import Financing from '../pages/Financing';

const zeroHome = {
  id: '43372',
  model_name: 'Premier / Creole',
  manufacturer: 'Champion Homes',
  status: 'Available',
  inventory_kind: 'available_now',
  display_price: '$0',
  price_value: 129900,
  image_url: 'https://lot.example/creole.jpg',
  specs: { beds: 4, baths: 2, sq_ft: 1600 },
};

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

describe('call for price and public page headings', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url) === '/api/marketing/inventory-context') {
        return response({ success: true, homes: [zeroHome] });
      }
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  it('renders Call for Price and the showroom phone when the price is $0', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse pathname="/inventory" />);
    expect(await screen.findAllByText('Call for Price')).not.toHaveLength(0);
    expect(screen.queryByText('$0')).not.toBeInTheDocument();
    expect(screen.queryByText('$129,900')).not.toBeInTheDocument();
    const phone = screen.getAllByRole('link', { name: '(281) 324-3020' });
    expect(phone.length).toBeGreaterThan(0);
    expect(phone[0]).toHaveAttribute('href', 'tel:+12813243020');
    const photos = [...document.querySelectorAll('img[src="https://lot.example/creole.jpg"]')];
    expect(photos.some((photo) => /Premier \/ Creole/i.test(photo.getAttribute('alt') || ''))).toBe(true);
  });

  it('uses one h1 on contact and labels the message form', () => {
    render(<Contact onBack={() => {}} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Contact Us' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Your Name')).toBeInTheDocument();
    expect(screen.getByLabelText('Phone Number')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'sales@texashomeoutlet.com' })).toHaveAttribute(
      'href',
      'mailto:sales@texashomeoutlet.com',
    );
  });

  it('keeps financing headings in order and leaves the lender TODO in the source', async () => {
    render(<Financing onBack={() => {}} />);
    expect(screen.getByRole('heading', { level: 1, name: /Manufactured Home Financing/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Chattel (Home-Only) Loan' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument();
    const pagesDir = path.resolve(process.cwd(), 'src/pages');
    const source = readFileSync(path.join(pagesDir, 'Financing.jsx'), 'utf8');
    expect(source).toMatch(/TODO\(owner\): confirm which lenders/);
    for (const page of ['Warranty.jsx', 'Delivery.jsx', 'FAQ.jsx']) {
      const text = readFileSync(path.join(pagesDir, page), 'utf8');
      expect(text).toMatch(/TODO\(owner\)/);
    }
  });
});
