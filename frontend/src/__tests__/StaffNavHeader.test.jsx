import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from '../App';
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

    const header = document.querySelector('header.overflow-x-hidden');
    expect(header).toBeTruthy();
    const logo = header.querySelector('h1');
    expect(logo.className).toMatch(/whitespace-nowrap/);
    expect(logo.className).toMatch(/truncate/);

    expect(screen.getByTestId('desktop-sign-out')).toHaveTextContent('Sign out');
    expect(screen.getAllByRole('button', { name: 'Sign out' }).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole('button', { name: /^More$/ })[0]);
    expect(screen.getByRole('menuitem', { name: 'Ops Copilot' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Numbers' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Sign out' })).toBeNull();
  });

  it('lands staff on the simple home instead of a loading analytics screen', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    expect(await screen.findByRole('heading', { name: 'Staff Home' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Add Photos/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Manage Homes/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Documents/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Leads/ }).length).toBeGreaterThan(0);
    expect(screen.queryByText('Loading analytics...')).toBeNull();
  });
});
