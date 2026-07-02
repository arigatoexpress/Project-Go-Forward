import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';

// App calls useToast(), so it must render inside a ToastProvider (as in main.jsx).
function renderApp() {
  return render(
    <ToastProvider>
      <App />
    </ToastProvider>,
  );
}

// Regression guard for the admin-lockout P0: the PIN box used to strip every
// non-digit and cap at 8 chars (`value.replace(/\D/g,'').slice(0, 8)`), so the
// configured 16-char ALPHANUMERIC backend PIN could not be typed or pasted and
// every admin was locked out. The field must now preserve the value verbatim
// (no stripping, no truncation under ADMIN_PIN_MAXLEN) and POST it to
// /api/admin/verify exactly.

const ALPHANUMERIC_PIN = 'Ab3xK9mZ2qLp7WnT'; // 16 chars, mixed case + digits
const EIGHT_DIGIT_PIN = '48329170';

// App fires several mount-time fetches (session, admin/check, passkey status…).
// Resolve them all generically so the component mounts cleanly; we then assert
// only on the /api/admin/verify call the PIN form makes.
function mockFetch() {
  return vi.fn((url) => {
    if (typeof url === 'string' && url.includes('/api/admin/verify')) {
      return Promise.resolve({ ok: true, json: async () => ({ success: false, error: 'Incorrect PIN. Please try again.' }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

function openPinModal() {
  fireEvent.click(screen.getByRole('button', { name: /Admin access/i }));
  return screen.getByLabelText('Admin PIN');
}

describe('Admin PIN field', () => {
  beforeEach(() => {
    global.fetch = mockFetch();
    // jsdom lacks matchMedia; App reads it for the dark-mode toggle.
    if (!window.matchMedia) {
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    }
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('preserves a 16-char alphanumeric value verbatim (no digit-strip, no 8-cap)', () => {
    renderApp();
    const input = openPinModal();

    fireEvent.change(input, { target: { value: ALPHANUMERIC_PIN } });

    // Old behavior would have produced "39297" (digits only, then capped at 8);
    // the field must now hold the exact alphanumeric value.
    expect(input.value).toBe(ALPHANUMERIC_PIN);
    expect(input.value.length).toBe(16);
  });

  it('accepts a pasted 16-char alphanumeric value intact', () => {
    renderApp();
    const input = openPinModal();

    // Simulate paste: the change event carries the full pasted string.
    fireEvent.change(input, { target: { value: ALPHANUMERIC_PIN } });

    expect(input.value).toBe(ALPHANUMERIC_PIN);
  });

  it('still accepts an 8-digit numeric PIN', () => {
    renderApp();
    const input = openPinModal();

    fireEvent.change(input, { target: { value: EIGHT_DIGIT_PIN } });

    expect(input.value).toBe(EIGHT_DIGIT_PIN);
  });

  it('uses a text input (not numeric) so mobile keyboards allow letters, capped at 64', () => {
    renderApp();
    const input = openPinModal();

    expect(input.getAttribute('inputmode')).toBe('text');
    expect(input.getAttribute('maxlength')).toBe('64');
    expect(input.getAttribute('type')).toBe('password');
  });

  it('POSTs the exact alphanumeric PIN to /api/admin/verify on submit', async () => {
    renderApp();
    const input = openPinModal();

    fireEvent.change(input, { target: { value: ALPHANUMERIC_PIN } });
    fireEvent.click(screen.getByRole('button', { name: /Unlock/i }));

    await waitFor(() => {
      const verifyCall = global.fetch.mock.calls.find(
        ([u]) => typeof u === 'string' && u.includes('/api/admin/verify'),
      );
      expect(verifyCall).toBeTruthy();
      const body = JSON.parse(verifyCall[1].body);
      expect(body.pin).toBe(ALPHANUMERIC_PIN);
    });
  });

  it('trims surrounding whitespace from the submitted PIN', async () => {
    renderApp();
    const input = openPinModal();

    fireEvent.change(input, { target: { value: `  ${ALPHANUMERIC_PIN}  ` } });
    fireEvent.click(screen.getByRole('button', { name: /Unlock/i }));

    await waitFor(() => {
      const verifyCall = global.fetch.mock.calls.find(
        ([u]) => typeof u === 'string' && u.includes('/api/admin/verify'),
      );
      expect(verifyCall).toBeTruthy();
      expect(JSON.parse(verifyCall[1].body).pin).toBe(ALPHANUMERIC_PIN);
    });
  });

  it('keeps the Unlock button disabled when the field is empty', () => {
    renderApp();
    openPinModal();

    expect(screen.getByRole('button', { name: /Unlock/i })).toBeDisabled();
  });

  it('points staff to email-code recovery when the shared PIN fails', async () => {
    renderApp();
    const input = openPinModal();

    fireEvent.change(input, { target: { value: ALPHANUMERIC_PIN } });
    fireEvent.click(screen.getByRole('button', { name: /Unlock/i }));

    expect(await screen.findByText(/If the shared PIN expired, use Email me a sign-in code below/i)).toBeInTheDocument();
  });
});
