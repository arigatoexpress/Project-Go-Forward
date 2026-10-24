import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from '../App';
import { clearStaffDrafts, readStaffDrafts, writeStaffDrafts } from '../components/ad-studio/staffDraftStorage';
import { ToastProvider } from '../components/Toast';

vi.mock('../utils/analytics', async () => {
  const actual = await vi.importActual('../utils/analytics');
  return { ...actual, attachPhoneClickTracking: vi.fn(() => () => {}), trackEvent: vi.fn() };
});

function stubStaffSession() {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const path = String(url);
    const body = path === '/api/admin/check'
      ? { valid: true }
      : { success: true, inventory: [], homes: [], messages: [] };
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  }));
}

describe('staff header at laptop widths', () => {
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
    stubStaffSession();
    window.history.replaceState({}, '', '/staff-home');
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('uses distinct public vs staff home labels and keeps Sign out reachable', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    expect(await screen.findByRole('heading', { name: 'Staff Home' })).toBeInTheDocument();

    expect(screen.getAllByRole('button', { name: 'Homes for Sale' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: 'Manage Homes' }).length).toBeGreaterThan(0);
    expect(screen.queryAllByRole('button', { name: /^Inventory$/ })).toHaveLength(0);

    const header = document.querySelector('header.sticky');
    expect(header).toBeTruthy();
    expect(header.className).toMatch(/overflow-visible/);
    expect(header.className).not.toMatch(/overflow-x-hidden/);
    const logo = header.querySelector('[aria-label$="home"] span');
    expect(logo).toBeTruthy();
    expect(logo.tagName).toBe('SPAN');
    expect(logo.className).toMatch(/whitespace-nowrap/);
    expect(logo.className).toMatch(/truncate/);
    expect(logo.parentElement.className).toMatch(/overflow-hidden/);

    expect(screen.getByTestId('desktop-sign-out')).toHaveTextContent('Sign out');
    expect(screen.getAllByRole('button', { name: 'Sign out' }).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole('button', { name: /^More$/ })[0]);
    const moreMenu = screen.getByTestId('staff-more-menu');
    expect(moreMenu).toBeInTheDocument();
    expect(moreMenu.closest('header')?.className).toMatch(/overflow-visible/);
    expect(screen.getByRole('menuitem', { name: 'Ops Copilot' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Numbers' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Sign out' })).toBeNull();
  });

  it('clears saved Ad Studio drafts when staff sign out', async () => {
    document.cookie = 'tho_csrf_token=staff-session; path=/';
    window.localStorage.setItem('tho_passkey_email', 'pat@texashomeoutlet.com');
    writeStaffDrafts([{ script_text: 'Look at this home' }]);
    expect(readStaffDrafts()).toHaveLength(1);

    render(<ToastProvider><App /></ToastProvider>);
    fireEvent.click(await screen.findByTestId('desktop-sign-out'));

    expect(readStaffDrafts()).toEqual([]);
    const leftover = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key && key.startsWith('tho-ad-studio-drafts:')) leftover.push(key);
    }
    expect(leftover).toEqual([]);
    document.cookie = 'tho_csrf_token=; Max-Age=0; path=/';
    clearStaffDrafts();
  });

  it('lands staff on the simple home instead of a loading analytics screen', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    expect(await screen.findByRole('heading', { name: 'Staff Home' })).toBeInTheDocument();
    expect(screen.queryAllByRole('button', { name: 'Save sign-in on this phone' })).toHaveLength(0);
    expect(screen.queryAllByRole('button', { name: 'Add a passkey' })).toHaveLength(0);
    expect(screen.getAllByRole('button', { name: /Add Photos/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Manage Homes/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Documents/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Leads/ }).length).toBeGreaterThan(0);
    expect(screen.queryByText('Loading analytics...')).toBeNull();
  });
});
