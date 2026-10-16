import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';

const homes = [
  {
    id: '44490',
    model_name: 'PRE-OWNED / Big Blue',
    manufacturer: 'Pre-Owned',
    status: 'Pre-Owned',
    inventory_kind: 'pre_owned',
    display_price: 'Call for Price',
    image_url: 'https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg',
    real_photos: [
      'https://lot.example/44490.jpg',
      'https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg',
    ],
    gallery_images: [
      'https://cdn.example/catalog.jpg',
      'https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg',
    ],
    floor_plan_url: 'https://lot.example/plan.jpg',
    specs: { beds: 3, baths: 2, sq_ft: 1152 },
  },
  {
    id: 'floorplan-223034',
    legacy_plan_id: '223034',
    model_name: 'Skyliner 4732B',
    manufacturer: 'Skyline',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    display_price: 'Call for Price',
    detail_url: '/plan/223034/skyliner/4732b/',
    specs: { beds: 3, baths: 2 },
  },
];

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

describe('unique city, floorplan, and home pages after hydration', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url) === '/api/marketing/inventory-context') return response({ success: true, homes });
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  it('keeps the Humble city title and H1 instead of the generic Huffman inventory title', async () => {
    window.history.replaceState({}, '', '/manufactured-homes-in-humble-tx');
    render(<InventoryBrowse pathname="/manufactured-homes-in-humble-tx" />);
    await waitFor(() => {
      expect(screen.getByRole('heading', {
        level: 1,
        name: 'Manufactured & Mobile Homes in Humble, TX',
      })).toBeInTheDocument();
    });
    expect(document.title).toContain('Humble, TX');
    expect(document.title).not.toContain('Homes for Sale in Huffman, TX');
    expect(document.querySelector('link[rel="canonical"]')?.getAttribute('href')).toContain('/manufactured-homes-in-humble-tx');
    expect(screen.getByText(/delivers new manufactured and mobile homes to Humble, TX/i)).toBeInTheDocument();
  });

  it('keeps the floorplan title and H1 on a plan URL', async () => {
    window.history.replaceState({}, '', '/plan/223034/skyliner/4732b/');
    render(<InventoryBrowse pathname="/plan/223034/skyliner/4732b/" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Skyliner 4732B' })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toContain('Skyliner 4732B'));
    expect(document.title).not.toContain('Homes for Sale in Huffman, TX');
  });

  it('renders an in-stock home page with its own title, price, photos, and card links', async () => {
    window.history.replaceState({}, '', '/homes/44490-pre-owned-big-blue');
    render(<InventoryBrowse pathname="/homes/44490-pre-owned-big-blue" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'PRE-OWNED / Big Blue' })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toContain('PRE-OWNED / Big Blue'));
    expect(screen.getAllByText('Call for Price').length).toBeGreaterThan(0);
    expect(document.querySelector('img[src="https://lot.example/44490.jpg"]')).not.toBeNull();
    expect(document.querySelector('img[src="https://cdn.example/catalog.jpg"]')).toBeNull();
    expect(document.querySelector('img[src="https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1391/showroom.jpg"]')).toBeNull();
    const details = screen.getAllByRole('link', { name: /View Details|View PRE-OWNED/i });
    expect(details.some((link) => link.getAttribute('href') === '/homes/44490-pre-owned-big-blue')).toBe(true);
  });

  it('puts call, text, and apply for that home above the marketing search', async () => {
    window.history.replaceState({}, '', '/homes/44490-pre-owned-big-blue');
    render(<InventoryBrowse pathname="/homes/44490-pre-owned-big-blue" />);
    const summary = await screen.findByRole('region', { name: 'Price and next steps' });
    const call = within(summary).getByRole('link', { name: /Call \(281\) 324-3020/i });
    const text = within(summary).getByRole('link', { name: /Text \(281\) 324-3020/i });
    expect(call).toHaveAttribute('href', 'tel:+12813243020');
    expect(text.getAttribute('href')).toMatch(/^sms:\+12813243020/);
    expect(within(summary).getByRole('button', { name: /Apply for financing/i })).toBeInTheDocument();
    expect(within(summary).getByText('Call for Price')).toBeInTheDocument();
    const search = screen.getByRole('textbox', { name: 'Search homes' });
    expect(summary.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const photos = [...document.querySelectorAll('article img[src="https://lot.example/44490.jpg"]')];
    expect(photos.length).toBeGreaterThan(0);
    expect(photos.every((photo) => summary.compareDocumentPosition(photo) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it('shows exactly one call, text, and apply set at each breakpoint', async () => {
    window.history.replaceState({}, '', '/homes/44490-pre-owned-big-blue');
    render(<InventoryBrowse pathname="/homes/44490-pre-owned-big-blue" />);
    const summary = await screen.findByRole('region', { name: 'Price and next steps' });
    const article = document.querySelector('article');
    expect(article).not.toBeNull();

    const sets = [summary, article].map((root) => {
      const actions = root.querySelector('.tho-listing-ctas');
      expect(actions).not.toBeNull();
      return {
        root,
        actions,
        call: within(actions).getByRole('link', { name: /Call \(281\) 324-3020/i }),
        text: within(actions).getByRole('link', { name: /Text \(281\) 324-3020/i }),
        apply: within(actions).getByRole('button', { name: /Apply for financing/i }),
      };
    });
    expect(sets).toHaveLength(2);

    for (const breakpoint of ['mobile', 'desktop']) {
      const visible = sets.filter((set) => (
        shownAt(set.call, breakpoint)
        && shownAt(set.text, breakpoint)
        && shownAt(set.apply, breakpoint)
      ));
      expect(visible).toHaveLength(1);
    }

    const [summarySet, articleSet] = sets;
    expect(summary.contains(summarySet.call)).toBe(true);
    expect(article.contains(articleSet.call)).toBe(true);
    expect(shownAt(summarySet.call, 'mobile')).toBe(true);
    expect(shownAt(summarySet.call, 'desktop')).toBe(false);
    expect(shownAt(articleSet.call, 'mobile')).toBe(false);
    expect(shownAt(articleSet.call, 'desktop')).toBe(true);
    expect(shownAt(articleSet.text, 'desktop')).toBe(true);
    expect(shownAt(articleSet.apply, 'desktop')).toBe(true);
    expect(articleSet.call).toHaveAttribute('href', 'tel:+12813243020');
    expect(articleSet.text.getAttribute('href')).toMatch(/^sms:\+12813243020/);

    const summaryPrice = within(summary).getByText('Call for Price');
    expect(shownAt(summaryPrice, 'mobile')).toBe(true);
    expect(shownAt(summaryPrice, 'desktop')).toBe(false);
    const articlePrice = within(article).getByText('Call for Price');
    expect(articlePrice.className).toContain('hidden');
    expect(articlePrice.className).toContain('lg:block');
    expect(shownAt(articlePrice, 'mobile')).toBe(false);
    expect(shownAt(articlePrice, 'desktop')).toBe(true);
  });
});

const LG_SHOW_CLASSES = ['lg:block', 'lg:flex', 'lg:grid', 'lg:inline', 'lg:inline-block', 'lg:inline-flex'];

function shownAt(element, breakpoint) {
  let mobile = true;
  let desktop = true;
  for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
    const classes = new Set(String(node.className || '').split(/\s+/).filter(Boolean));
    const baseHidden = classes.has('hidden');
    const lgHidden = classes.has('lg:hidden');
    const lgShow = LG_SHOW_CLASSES.some((name) => classes.has(name));
    if (baseHidden && !lgShow) {
      mobile = false;
      desktop = false;
    } else if (baseHidden) {
      mobile = false;
    }
    if (lgHidden) desktop = false;
  }
  return breakpoint === 'desktop' ? desktop : mobile;
}
