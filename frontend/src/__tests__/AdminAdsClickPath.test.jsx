import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';
import {
  FIXTURE_HOME,
  FIXTURE_PIN,
  STUDIO_HOME,
  internalDraftStatus,
} from './fixtures/adminAdsClickPath';

vi.mock('../utils/analytics', async () => {
  const actual = await vi.importActual('../utils/analytics');
  return {
    ...actual,
    attachPhoneClickTracking: vi.fn(() => () => {}),
    trackEvent: vi.fn(),
  };
});

function jsonResponse(data, { ok = true, status = ok ? 200 : 401 } = {}) {
  return Promise.resolve({
    ok,
    status,
    json: async () => data,
  });
}

function requestPath(url) {
  return String(url);
}

describe('admin, listing, Ads, and AI Ads Studio click path', () => {
  let signedIn;

  beforeEach(() => {
    signedIn = false;
    window.history.replaceState({}, '', '/staff');
    window.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }));
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const path = requestPath(url);
      const method = String(options.method || 'GET').toUpperCase();
      if (path.includes('/api/admin/verify') && method === 'POST') {
        const body = JSON.parse(options.body || '{}');
        if (body.pin === FIXTURE_PIN) {
          signedIn = true;
          return jsonResponse({ success: true });
        }
        return jsonResponse({ success: false, error: 'That PIN did not work.' });
      }
      if (path.includes('/api/admin/check')) return jsonResponse({ valid: signedIn });
      if (path.includes('/api/admin/sign-in/options')) {
        return jsonResponse({ email_ready: true, passkey_sign_in: false });
      }
      if (path.includes('/api/admin/passkey/status')) {
        return jsonResponse({ enabled: false, store_ready: false, has_keys: false });
      }
      if (path.includes('/api/admin/google-ads/draft')) return jsonResponse(internalDraftStatus());
      if (path.includes('/photos')) return jsonResponse({ photos: [] });
      if (path.includes('/api/inventory')) {
        if (method !== 'GET') {
          return jsonResponse({ success: false, error: 'This click path does not save homes.' }, { ok: false, status: 405 });
        }
        return jsonResponse({ success: true, inventory: [FIXTURE_HOME] });
      }
      if (path.includes('/api/marketing/inventory-context')) {
        return jsonResponse({ success: true, homes: [STUDIO_HOME] });
      }
      if (path.includes('/api/marketing/voiceover-voices')) {
        return jsonResponse({ success: true, voices: [] });
      }
      if (path.includes('/api/marketing/gcp-readiness')) {
        return jsonResponse({ success: true, ready: false });
      }
      return jsonResponse({ success: true, homes: [], messages: [], voices: [] });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  async function openStaffTool(name) {
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    const menu = await screen.findByTestId('staff-more-menu');
    fireEvent.click(within(menu).getByRole('menuitem', { name }));
  }

  it('signs in with a fixture and walks listing admin, Ads, and AI Ads Studio', async () => {
    render(<ToastProvider><App /></ToastProvider>);

    expect(await screen.findByRole('heading', { name: 'Staff sign-in' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Use backup PIN' }));
    fireEvent.change(screen.getByLabelText('Backup PIN'), { target: { value: FIXTURE_PIN } });
    fireEvent.click(screen.getByRole('button', { name: 'Unlock' }));

    expect(await screen.findByRole('heading', { name: 'Staff Home' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Staff sign-in' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Add or change a home/ }));
    expect(await screen.findByRole('heading', { name: /Homes on the website/ })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Clickpath Single' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/manage-inventory');

    fireEvent.click(screen.getByRole('button', { name: 'Change this home' }));
    expect(await screen.findByRole('heading', { name: 'Change this home' })).toBeInTheDocument();
    expect(screen.getByLabelText('Home name')).toHaveValue('Clickpath Single');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByLabelText('Home name')).not.toBeInTheDocument();

    await openStaffTool('Google Ads');
    expect(await screen.findByRole('heading', { name: '1. Create' })).toBeInTheDocument();
    expect(screen.getByLabelText('Who should see this ad?')).toBeInTheDocument();
    const adsTab = screen.getByRole('button', { name: 'Google Ads' });
    expect(adsTab).toHaveAttribute('aria-current', 'page');
    expect(window.location.pathname).toBe('/ad-studio');
    expect(screen.queryByRole('button', { name: /turn ads on|publish/i })).not.toBeInTheDocument();

    await openStaffTool('Ad Studio');
    expect(window.location.pathname).toBe('/studio');
    expect(await screen.findByText("Tex's Ad Studio")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create Ad' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Google Ads' })).not.toHaveAttribute('aria-current', 'page');
    expect(screen.getByText('Nothing is posted from here.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Choose a home' }));
    expect(await screen.findByRole('heading', { name: 'Choose a home' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Clickpath Single/ }));
    expect(screen.getByLabelText('Home name')).toHaveValue('Clickpath Single');
    expect(screen.getByLabelText('Price')).toHaveValue('');

    const calls = vi.mocked(fetch).mock.calls.map(([url, options]) => ({
      url: String(url),
      method: String(options?.method || 'GET').toUpperCase(),
    }));
    expect(calls.filter((call) => call.url.includes('/api/admin/verify'))).toHaveLength(1);
    expect(calls.some((call) => call.method === 'PUT' || call.method === 'DELETE')).toBe(false);
    expect(calls.some((call) => call.url.includes('/api/marketing/generate'))).toBe(false);
    expect(calls.some((call) => call.url.includes('/api/marketing/schedule'))).toBe(false);
    expect(calls.some((call) => call.url.includes('/api/admin/google-ads/paused-create'))).toBe(false);
    expect(calls.some((call) => call.url.includes('/api/admin/google-ads/server-validation'))).toBe(false);
  });
});
