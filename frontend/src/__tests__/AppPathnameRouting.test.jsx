import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';
import { navigateDocument } from '../utils/documentNavigation';
import { trackEvent } from '../utils/analytics';

vi.mock('../utils/analytics', async () => {
  const actual = await vi.importActual('../utils/analytics');
  return {
    ...actual,
    attachPhoneClickTracking: vi.fn(() => () => {}),
    trackEvent: vi.fn(),
  };
});

vi.mock('../utils/documentNavigation', () => ({
  navigateDocument: vi.fn(),
}));

const homes = [
  {
    id: 'single-app',
    model_name: 'App Single',
    classification: ' single-wide ',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    specs: { beds: 3, baths: 2 },
    image_url: '',
    real_photos: [],
    gallery_images: [],
  },
  {
    id: 'double-app',
    model_name: 'App Double',
    classification: ' DOUBLE_section ',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    specs: { beds: 4, baths: 2 },
    image_url: '',
    real_photos: [],
    gallery_images: [],
  },
];

function response(data, ok = true) {
  return Promise.resolve({ ok, json: async () => data });
}

describe('App pathname-aware routing', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/single-wide');
    window.scrollTo = vi.fn();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }));
    vi.mocked(trackEvent).mockClear();
    vi.mocked(navigateDocument).mockClear();
    vi.stubGlobal('fetch', vi.fn((url) => {
      const path = String(url);
      if (path === '/api/marketing/inventory-context') return response({ success: true, homes });
      if (path === '/api/admin/check') return response({ valid: false });
      if (path.startsWith('/api/chat/session/')) return response({ success: true, messages: [] });
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('keeps category links native and synchronizes popstate title, page views, and filters', async () => {
    render(<ToastProvider><App /></ToastProvider>);

    expect((await screen.findAllByText('App Single')).length).toBeGreaterThan(0);
    expect(screen.queryAllByText('App Double')).toHaveLength(0);
    expect(document.title).toContain('Single Wide Mobile & Manufactured Homes');
    expect(trackEvent).toHaveBeenCalledWith('page_viewed', {
      page: 'inventory',
      page_path: '/single-wide',
    });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search homes' }), {
      target: { value: 'App Single' },
    });
    let componentPreventedNavigation;
    document.addEventListener('click', (event) => {
      componentPreventedNavigation = event.defaultPrevented;
      event.preventDefault();
    }, { once: true });
    fireEvent.click(screen.getByRole('link', { name: 'Double Wide Homes' }));
    expect(componentPreventedNavigation).toBe(false);
    expect(window.location.pathname).toBe('/single-wide');

    window.history.pushState({}, '', '/double-wide');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect((await screen.findAllByText('App Double')).length).toBeGreaterThan(0);
    expect(screen.queryAllByText('App Single')).toHaveLength(0);
    expect(screen.getByRole('textbox', { name: 'Search homes' })).toHaveValue('');
    expect(document.title).toContain('Double Wide Mobile & Manufactured Homes');
    expect(trackEvent).toHaveBeenCalledWith('page_viewed', {
      page: 'inventory',
      page_path: '/double-wide',
    });

    window.history.pushState({}, '', '/single-wide');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect((await screen.findAllByText('App Single')).length).toBeGreaterThan(0);
    expect(screen.queryAllByText('App Double')).toHaveLength(0);
    expect(document.title).toContain('Single Wide Mobile & Manufactured Homes');
    expect(trackEvent).toHaveBeenLastCalledWith('page_viewed', {
      page: 'inventory',
      page_path: '/single-wide',
    });
  });

  it('uses a full document boundary when leaving a category through app navigation', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    await screen.findAllByText('App Single');

    fireEvent.click(screen.getByRole('button', { name: /— home$/i }));

    expect(navigateDocument).toHaveBeenCalledWith('/');
    expect(window.location.pathname).toBe('/single-wide');
  });
});
