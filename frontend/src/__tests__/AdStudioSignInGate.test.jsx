import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';
import { readStaffDrafts, writeStaffDrafts } from '../components/ad-studio/staffDraftStorage';

const SIGN_IN_CHECK_TIMEOUT_MS = 8000;

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
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function httpResponse(data, ok = true) {
  return {
    ok,
    status: ok ? 200 : 401,
    json: async () => data,
  };
}

function resetStaffDraftState() {
  document.cookie = 'tho_csrf_token=; Max-Age=0; path=/';
  window.localStorage.clear();
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
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    resetStaffDraftState();
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

  it('shows the locked page when the session check returns 401', async () => {
    render(<ToastProvider><App /></ToastProvider>);

    adminCheck.resolve(httpResponse({ detail: 'Not signed in' }, false));

    expect(await screen.findByRole('heading', { name: 'Ad Studio is for staff' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Staff sign-in' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Checking your sign-in...' })).not.toBeInTheDocument();
    expect(screen.queryByText("Tex's Ad Studio")).not.toBeInTheDocument();
  });

  it('shows the locked page when the session check fails', async () => {
    render(<ToastProvider><App /></ToastProvider>);

    adminCheck.reject(new TypeError('Failed to fetch'));

    expect(await screen.findByRole('heading', { name: 'Ad Studio is for staff' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Staff sign-in' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Checking your sign-in...' })).not.toBeInTheDocument();
    expect(screen.queryByText("Tex's Ad Studio")).not.toBeInTheDocument();
  });

  it('times out a hung check to the locked page with the Staff sign-in button', async () => {
    document.cookie = 'tho_csrf_token=hung-session; path=/';
    writeStaffDrafts([{ script_text: 'Unsent flyer' }]);
    vi.useFakeTimers();
    render(<ToastProvider><App /></ToastProvider>);

    expect(screen.getByRole('heading', { name: 'Checking your sign-in...' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Staff sign-in' })).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SIGN_IN_CHECK_TIMEOUT_MS - 1);
    });
    expect(screen.getByRole('heading', { name: 'Checking your sign-in...' })).toBeInTheDocument();
    expect(readStaffDrafts()).toEqual([{ script_text: 'Unsent flyer' }]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(screen.getByRole('heading', { name: 'Ad Studio is for staff' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Staff sign-in' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Checking your sign-in...' })).not.toBeInTheDocument();
    expect(screen.queryByText("Tex's Ad Studio")).not.toBeInTheDocument();
    expect(readStaffDrafts()).toEqual([]);
  });

  it('clears saved staff drafts when the session check is invalid', async () => {
    document.cookie = 'tho_csrf_token=session-a; path=/';
    writeStaffDrafts([{ script_text: 'A draft' }]);
    document.cookie = 'tho_csrf_token=session-b; path=/';
    writeStaffDrafts([{ script_text: 'B draft' }]);

    render(<ToastProvider><App /></ToastProvider>);
    adminCheck.resolve(httpResponse({ valid: false }));

    expect(await screen.findByRole('heading', { name: 'Ad Studio is for staff' })).toBeInTheDocument();
    document.cookie = 'tho_csrf_token=session-a; path=/';
    expect(readStaffDrafts()).toEqual([]);
    document.cookie = 'tho_csrf_token=session-b; path=/';
    expect(readStaffDrafts()).toEqual([]);
  });
});
