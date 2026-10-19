import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
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

function jsonResponse(data, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 401,
    json: async () => data,
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('Ad Studio sign-in gate', () => {
  let adminCheck;

  beforeEach(() => {
    adminCheck = deferred();
    window.history.replaceState({}, '', '/ad-studio');
    window.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }));
    vi.stubGlobal('fetch', vi.fn((url) => {
      const path = String(url);
      if (path === '/api/admin/check') return adminCheck.promise;
      return jsonResponse({ success: true, homes: [], messages: [], voices: [] });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('shows a neutral checking state while the session check is pending', async () => {
    render(<ToastProvider><App /></ToastProvider>);

    expect(screen.getByRole('heading', { name: 'Checking your sign-in...' })).toBeInTheDocument();
    expect(screen.getByText(/whether this browser is signed in/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ad Studio is for staff' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Staff sign-in' })).not.toBeInTheDocument();
    expect(screen.queryByText("Tex's Ad Studio")).not.toBeInTheDocument();

    await new Promise((resolve) => { setTimeout(resolve, 150); });

    expect(screen.getByRole('heading', { name: 'Checking your sign-in...' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Staff sign-in' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ad Studio is for staff' })).not.toBeInTheDocument();
  });

  it('opens Ad Studio after the session check says signed in', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    expect(screen.getByRole('heading', { name: 'Checking your sign-in...' })).toBeInTheDocument();

    adminCheck.resolve(jsonResponse({ valid: true }));

    expect(await screen.findByText("Tex's Ad Studio")).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Checking your sign-in...' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ad Studio is for staff' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Staff sign-in' })).not.toBeInTheDocument();
  });

  it('shows the locked page after the session check says signed out', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    expect(screen.getByRole('heading', { name: 'Checking your sign-in...' })).toBeInTheDocument();

    adminCheck.resolve(jsonResponse({ valid: false }));

    expect(await screen.findByRole('heading', { name: 'Ad Studio is for staff' })).toBeInTheDocument();
    expect(screen.getByText(/stays locked until you sign in/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Checking your sign-in...' })).not.toBeInTheDocument();
    expect(screen.queryByText("Tex's Ad Studio")).not.toBeInTheDocument();
  });
});
