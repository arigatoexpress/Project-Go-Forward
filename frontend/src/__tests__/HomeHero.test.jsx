import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';
import { SERVICE_AREA_CITIES } from '../constants';

const homes = [
  { id: '1', model_name: 'Lot Home', inventory_kind: 'available_now', specs: { beds: 3, baths: 2 } },
  { id: '2', model_name: 'Lot Home 2', inventory_kind: 'available_now', specs: {} },
  { id: '3', model_name: 'Used', inventory_kind: 'pre_owned', status: 'Pre-Owned', specs: {} },
  { id: '4', model_name: 'Plan', inventory_kind: 'orderable_floorplan', status: 'Orderable', specs: {} },
];

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

function citySlug(city) {
  return city.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

describe('home page hero', () => {
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
  });

  it('leads with call, Tex, and search, and lists towns under Areas we serve', async () => {
    render(<InventoryBrowse pathname="/" onAskTex={() => {}} />);
    const [call] = await screen.findAllByRole('link', { name: /Call \(281\) 324-3020/i });
    const tex = screen.getByRole('button', { name: /Talk to Tex/i });
    const search = screen.getByRole('textbox', { name: 'Search homes' });
    const areas = screen.getByRole('heading', { level: 2, name: 'Areas we serve' });

    expect(call.compareDocumentPosition(tex) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tex.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(search.compareDocumentPosition(areas) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const hero = screen.getByRole('heading', { level: 1 }).closest('section');
    expect(hero.querySelector('a[href="/manufactured-homes-in-humble-tx"]')).toBeNull();

    const areaSection = areas.closest('section');
    for (const city of SERVICE_AREA_CITIES) {
      const link = areaSection.querySelector(`a[href="/manufactured-homes-in-${citySlug(city)}-tx"]`);
      expect(link).not.toBeNull();
      expect(link.className).toContain('min-h-11');
    }
  });

  it('shows each catalog bucket from the loaded homes and no mixed houses total', async () => {
    render(<InventoryBrowse pathname="/" onAskTex={() => {}} />);
    expect(await screen.findByText('2 on the lot, 1 pre-owned, and 1 orderable floorplan.')).toBeInTheDocument();
    const counts = screen.getByRole('group', { name: 'Catalog counts' });
    expect(within(counts).getByText('On the lot').previousElementSibling).toHaveTextContent('2');
    expect(within(counts).getByText('Pre-owned').previousElementSibling).toHaveTextContent('1');
    expect(within(counts).getByText('Orderable plans').previousElementSibling).toHaveTextContent('1');
    expect(within(counts).queryByText('Houses')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'On the Lot (2)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pre-Owned (1)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Orderable (1)' })).toBeInTheDocument();
  });

  it('keeps town links in the inventory hub hero', async () => {
    render(<InventoryBrowse pathname="/inventory" onAskTex={() => {}} />);
    const nav = await screen.findByRole('navigation', { name: 'Home, inventory, and areas we serve' });
    expect(nav.querySelector('a[href="/manufactured-homes-in-humble-tx"]')).not.toBeNull();
    expect(screen.queryByRole('heading', { name: 'Areas we serve' })).not.toBeInTheDocument();
  });
});
