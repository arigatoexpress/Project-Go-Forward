import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';

vi.mock('../utils/analytics', async () => {
  const actual = await vi.importActual('../utils/analytics');
  return {
    ...actual,
    attachPhoneClickTracking: vi.fn(() => () => {}),
    trackEvent: vi.fn(),
  };
});

const home = { id: '43372', model_name: 'Starter Home', status: 'AVAILABLE', real_photos: [] };

function stubFetch({ adminValid }) {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const path = String(url);
    const body = path === '/api/admin/check'
      ? { valid: adminValid }
      : path === '/api/marketing/inventory-context'
        ? { success: true, homes: [home] }
        : path.endsWith('/photos')
          ? { photos: [] }
          : { success: true };
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  }));
}

describe('/photos staff route', () => {
  beforeEach(() => {
    window.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('opens the Photo Manager for signed-in staff', async () => {
    stubFetch({ adminValid: true });
    window.history.replaceState({}, '', '/photos');
    render(<ToastProvider><App /></ToastProvider>);

    expect(await screen.findByRole('heading', { name: 'Add Photos to Homes' })).toBeInTheDocument();
    expect(screen.queryByText(/wandered off the lot/i)).toBeNull();
    expect(window.location.pathname).toBe('/photos');
    await waitFor(() => expect(document.title).toMatch(/^Photos \|/));
  });

  it('asks signed-out visitors to sign in instead of showing the 404 page', async () => {
    stubFetch({ adminValid: false });
    window.history.replaceState({}, '', '/photos');
    render(<ToastProvider><App /></ToastProvider>);

    expect(await screen.findByRole('heading', { name: 'Staff sign-in' })).toBeInTheDocument();
    expect(screen.queryByText(/wandered off the lot/i)).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Add Photos to Homes' })).toBeNull();
  });

  it('puts /photos in the address bar when staff open Photos from the guide', async () => {
    stubFetch({ adminValid: true });
    window.history.replaceState({}, '', '/getting-started');
    render(<ToastProvider><App /></ToastProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /Open Photos/i }));

    expect(window.location.pathname).toBe('/photos');
    expect(await screen.findByRole('heading', { name: 'Add Photos to Homes' })).toBeInTheDocument();
  });

  it('gives every staff menu tool its own URL instead of falling back to /', async () => {
    stubFetch({ adminValid: true });
    window.history.replaceState({}, '', '/photos');
    render(<ToastProvider><App /></ToastProvider>);
    await screen.findByRole('heading', { name: 'Add Photos to Homes' });

    // Desktop nav lists public items first, so the second "Inventory" is the staff one.
    fireEvent.click(screen.getAllByRole('button', { name: /^Inventory$/ })[1]);
    expect(window.location.pathname).toBe('/manage-inventory');

    fireEvent.click(screen.getAllByRole('button', { name: /^Ops Copilot$/ })[0]);
    expect(window.location.pathname).toBe('/copilot');

    fireEvent.click(screen.getAllByRole('button', { name: /^Photos$/ })[0]);
    expect(window.location.pathname).toBe('/photos');
    expect(await screen.findByRole('heading', { name: 'Add Photos to Homes' })).toBeInTheDocument();
  });
});
