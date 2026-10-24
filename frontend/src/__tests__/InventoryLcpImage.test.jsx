import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';
import { STATIC_LCP_STALL_MS } from '../utils/staticLcp';

const HERO = '/api/inventory/photos/28102/hero.jpg';

const homes = [
  {
    id: '28102',
    model_name: 'Tru / Dogwood',
    manufacturer: 'Tru',
    status: 'Available',
    inventory_kind: 'available_now',
    display_price: 'Call for Price',
    specs: { beds: 3, baths: 2, sq_ft: 1200 },
    image_url: HERO,
    real_photos: [HERO],
  },
];

function backgroundHero() {
  return [...document.querySelectorAll('img')].find((img) => (
    img.id !== 'tho-lcp' && img.className.includes('absolute') && img.className.includes('inset-0')
  )) || null;
}

function paintLcp({ src = '/api/inventory/lcp-image', complete = false, naturalWidth = 0 } = {}) {
  const painted = document.createElement('img');
  painted.id = 'tho-lcp';
  painted.alt = '';
  if (src) painted.setAttribute('src', src);
  Object.defineProperty(painted, 'complete', { configurable: true, get: () => complete });
  Object.defineProperty(painted, 'naturalWidth', { configurable: true, get: () => naturalWidth });
  document.body.appendChild(painted);
  return painted;
}

describe('inventory hero when the server already painted LCP', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, homes }),
    }));
  });

  afterEach(() => {
    cleanup();
    document.getElementById('tho-lcp')?.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    window.history.replaceState({}, '', '/inventory');
  });

  it('keeps the static image and does not start a second high-priority hero', async () => {
    window.history.replaceState({}, '', '/inventory');
    const painted = document.createElement('img');
    painted.id = 'tho-lcp';
    painted.alt = '';
    document.body.appendChild(painted);
    render(<InventoryBrowse pathname="/inventory" />);

    expect(await screen.findByRole('heading', { name: /Home Inventory in / })).toBeTruthy();
    expect(document.getElementById('tho-lcp')).not.toBeNull();
    const competing = [...document.querySelectorAll('img[fetchpriority="high"]')]
      .filter((img) => img.id !== 'tho-lcp');
    expect(competing).toHaveLength(0);
    const spotlight = document.querySelector(`img[src="${HERO}"]`);
    expect(spotlight).not.toBeNull();
    expect(spotlight.getAttribute('fetchpriority')).toBe('low');
    expect(spotlight.getAttribute('loading')).toBe('lazy');
    expect(backgroundHero()).toBeNull();
    expect(painted.hidden).toBe(false);
  });

  it('keeps the original hero unmounted when the derived image loads', async () => {
    const painted = paintLcp();
    render(<InventoryBrowse pathname="/inventory" />);

    expect(await screen.findByRole('heading', { name: /Home Inventory in / })).toBeTruthy();
    expect(backgroundHero()).toBeNull();
    fireEvent.load(painted);
    expect(backgroundHero()).toBeNull();
    expect(painted.hidden).toBe(false);
    expect(painted.getAttribute('data-lcp-failed')).toBeNull();
    expect(painted.getAttribute('data-lcp-loaded')).toBe('1');
  });

  it('falls back to the original hero when the derived image errors', async () => {
    const painted = paintLcp();
    render(<InventoryBrowse pathname="/inventory" />);

    expect(await screen.findByRole('heading', { name: /Home Inventory in / })).toBeTruthy();
    expect(backgroundHero()).toBeNull();
    fireEvent.error(painted);

    const hero = await waitFor(() => {
      const found = backgroundHero();
      expect(found).not.toBeNull();
      return found;
    });
    expect(hero).not.toBeNull();
    expect(hero.getAttribute('src')).toBe(HERO);
    expect(hero.getAttribute('fetchpriority')).toBe('high');
    expect(painted.hidden).toBe(true);
    expect(painted.getAttribute('data-lcp-failed')).toBe('1');
  });

  it('falls back when the derived image already failed before render', async () => {
    const painted = paintLcp({ complete: true, naturalWidth: 0 });
    render(<InventoryBrowse pathname="/inventory" />);

    expect(await screen.findByRole('heading', { name: /Home Inventory in / })).toBeTruthy();
    // The heading can render before the mocked inventory fetch resolves, and
    // the hero needs the loaded homes. Wait for it instead of reading once.
    const hero = await waitFor(() => {
      const found = backgroundHero();
      expect(found).not.toBeNull();
      return found;
    });
    expect(hero.getAttribute('src')).toBe(HERO);
    expect(painted.hidden).toBe(true);
    expect(painted.getAttribute('data-lcp-failed')).toBe('1');
  });

  it('falls back when the derived image never loads', async () => {
    vi.useFakeTimers();
    const painted = paintLcp();
    render(<InventoryBrowse pathname="/inventory" />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole('heading', { name: /Home Inventory in / })).toBeTruthy();
    expect(backgroundHero()).toBeNull();
    expect(painted.hidden).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STATIC_LCP_STALL_MS);
    });

    const hero = backgroundHero();
    expect(hero).not.toBeNull();
    expect(hero.getAttribute('src')).toBe(HERO);
    expect(painted.hidden).toBe(true);
    expect(painted.getAttribute('data-lcp-failed')).toBe('1');
  });

  it('does not replace a derived image that loads before the stall window', async () => {
    vi.useFakeTimers();
    const painted = paintLcp();
    render(<InventoryBrowse pathname="/inventory" />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    fireEvent.load(painted);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STATIC_LCP_STALL_MS);
    });

    expect(backgroundHero()).toBeNull();
    expect(painted.hidden).toBe(false);
    expect(painted.getAttribute('data-lcp-failed')).toBeNull();
    expect(painted.getAttribute('data-lcp-loaded')).toBe('1');
  });

  it.each([
    ['/homes/28102-tru-dogwood'],
    ['/inventory-detail/28102'],
  ])('falls back to the original hero on %s when the derived image errors', async (path) => {
    window.history.replaceState({}, '', path);
    const painted = paintLcp();
    render(<InventoryBrowse pathname={path} />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Tru / Dogwood' })).toBeTruthy();
    expect(backgroundHero()).toBeNull();
    const articlePhoto = document.querySelector('article img');
    expect(articlePhoto).not.toBeNull();
    expect(articlePhoto.getAttribute('src')).toBe(HERO);
    expect(articlePhoto.getAttribute('fetchpriority')).toBe('low');

    fireEvent.error(painted);

    const hero = await waitFor(() => {
      const found = backgroundHero();
      expect(found).not.toBeNull();
      return found;
    });
    expect(hero).not.toBeNull();
    expect(hero.getAttribute('src')).toBe(HERO);
    expect(hero.getAttribute('fetchpriority')).toBe('high');
    expect(painted.hidden).toBe(true);
    expect(painted.getAttribute('data-lcp-failed')).toBe('1');
    expect(document.querySelector('article img').getAttribute('fetchpriority')).toBe('high');
  });
});
