import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';

// Email one-time-code admin login is the FALLBACK path alongside PIN + passkey.
// These tests assert the three contracts that matter for security/UX:
//   1. email -> request -> code -> verify authenticates (success path);
//   2. a disallowed/unknown email still shows the SAME generic message
//      (no account enumeration) — the backend always 200s the request;
//   3. a wrong code surfaces an error and does NOT authenticate.

function renderApp() {
  return render(
    <ToastProvider>
      <App />
    </ToastProvider>,
  );
}

// App fires mount-time fetches (session check, admin/check, passkey status).
// Resolve them generically; per-test we override the OTP endpoints.
function baseFetch() {
  return (url) => {
    if (typeof url === 'string' && url.includes('/api/admin/check')) {
      return Promise.resolve({ ok: true, json: async () => ({ valid: false }) });
    }
    if (typeof url === 'string' && url.includes('/api/admin/passkey/status')) {
      return Promise.resolve({ ok: true, json: async () => ({ enabled: true, has_keys: false, store_ready: true }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  };
}

function openEmailCodeFlow() {
  // Open the admin PIN modal, then switch to the email-code path.
  fireEvent.click(screen.getByRole('button', { name: /Admin access/i }));
  fireEvent.click(screen.getByRole('button', { name: /Email me a sign-in code/i }));
}

describe('Admin email one-time-code login', () => {
  beforeEach(() => {
    if (!window.matchMedia) {
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    }
    // jsdom implements neither of these; App calls them on render/scroll.
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = () => {};
    } else {
      vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
    }
    if (!window.scrollTo) {
      window.scrollTo = () => {};
    }
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('email -> send -> code -> verify authenticates the admin', async () => {
    const fallback = baseFetch();
    global.fetch = vi.fn((url, opts) => {
      if (typeof url === 'string' && url.includes('/api/admin/email-code/request')) {
        return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
      }
      if (typeof url === 'string' && url.includes('/api/admin/email-code/verify')) {
        return Promise.resolve({ ok: true, json: async () => ({ success: true, csrf_token: 'csrf-xyz' }) });
      }
      return fallback(url, opts);
    });

    renderApp();
    openEmailCodeFlow();

    // Enter authorized email, request the code.
    const emailInput = screen.getByLabelText(/email/i);
    fireEvent.change(emailInput, { target: { value: 'staff@texashomeoutlet.com' } });
    fireEvent.click(screen.getByRole('button', { name: /Send code/i }));

    // Generic confirmation appears + the code field shows.
    await screen.findByText(/a code is on its way/i);
    const codeInput = await screen.findByLabelText(/sign-in code/i);

    // The request hit the backend with the email.
    const requestCall = global.fetch.mock.calls.find(
      ([u]) => typeof u === 'string' && u.includes('/api/admin/email-code/request'),
    );
    expect(requestCall).toBeTruthy();
    expect(JSON.parse(requestCall[1].body).email).toBe('staff@texashomeoutlet.com');

    // Enter the code, verify.
    fireEvent.change(codeInput, { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /^Verify$/i }));

    await waitFor(() => {
      const verifyCall = global.fetch.mock.calls.find(
        ([u]) => typeof u === 'string' && u.includes('/api/admin/email-code/verify'),
      );
      expect(verifyCall).toBeTruthy();
      const body = JSON.parse(verifyCall[1].body);
      expect(body.email).toBe('staff@texashomeoutlet.com');
      expect(body.code).toBe('123456');
    });

    // On success the PIN/email modal closes (admin is authed).
    await waitFor(() => {
      expect(screen.queryByLabelText(/sign-in code/i)).not.toBeInTheDocument();
    });
  });

  it('shows the SAME generic message for a disallowed email (no enumeration)', async () => {
    const fallback = baseFetch();
    global.fetch = vi.fn((url, opts) => {
      if (typeof url === 'string' && url.includes('/api/admin/email-code/request')) {
        // Backend always 200s with a generic success — even for unknown emails.
        return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
      }
      return fallback(url, opts);
    });

    renderApp();
    openEmailCodeFlow();

    const emailInput = screen.getByLabelText(/email/i);
    fireEvent.change(emailInput, { target: { value: 'stranger@gmail.com' } });
    fireEvent.click(screen.getByRole('button', { name: /Send code/i }));

    // Identical generic copy — the UI must not reveal authorization status.
    await screen.findByText(/a code is on its way/i);
  });

  it('surfaces an error and does not authenticate on a wrong code', async () => {
    const fallback = baseFetch();
    global.fetch = vi.fn((url, opts) => {
      if (typeof url === 'string' && url.includes('/api/admin/email-code/request')) {
        return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
      }
      if (typeof url === 'string' && url.includes('/api/admin/email-code/verify')) {
        return Promise.resolve({ ok: false, status: 401, json: async () => ({ success: false, error: 'Invalid or expired code.' }) });
      }
      return fallback(url, opts);
    });

    renderApp();
    openEmailCodeFlow();

    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'staff@texashomeoutlet.com' } });
    fireEvent.click(screen.getByRole('button', { name: /Send code/i }));

    const codeInput = await screen.findByLabelText(/sign-in code/i);
    fireEvent.change(codeInput, { target: { value: '000000' } });
    fireEvent.click(screen.getByRole('button', { name: /^Verify$/i }));

    // Error is shown and the code field is still present (not authed).
    await screen.findByText(/Invalid or expired code/i);
    expect(screen.getByLabelText(/sign-in code/i)).toBeInTheDocument();
  });
});
