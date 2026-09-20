import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';

const GUIDANCE = 'Passkey sign-in did not finish. Try again, or choose Email me a sign-in code below.';
let getCredential;

async function openPasskeyLogin() {
  render(<ToastProvider><App /></ToastProvider>);
  fireEvent.click(screen.getByRole('button', { name: /Admin access/i }));
  return screen.findByRole('button', { name: /Sign in with Passkey/i });
}

describe('Admin passkey sign-in recovery', () => {
  beforeEach(() => {
    vi.stubGlobal('PublicKeyCredential', class {});
    getCredential = vi.fn();
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: { get: getCredential },
    });
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/admin/check') {
        return { ok: true, json: async () => ({ valid: false }) };
      }
      if (url === '/api/admin/passkey/status') {
        return { ok: true, json: async () => ({ enabled: true, has_keys: true, store_ready: true }) };
      }
      if (url === '/api/admin/passkey/login/begin') {
        return { ok: true, json: async () => ({ challenge: 'dGVzdA', allowCredentials: [] }) };
      }
      return { ok: true, json: async () => ({}) };
    }));
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    if (!window.matchMedia) {
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete navigator.credentials;
  });

  it.each(['NotAllowedError', 'AbortError'])('offers recovery after %s without granting access or sending email', async (name) => {
    getCredential.mockRejectedValue(new DOMException('Raw browser prompt detail', name));
    const button = await openPasskeyLogin();
    fireEvent.click(button);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(GUIDANCE);
    expect(button).toHaveAccessibleDescription(GUIDANCE);
    expect(button.parentElement.nextElementSibling).toBe(alert);
    expect(screen.queryByText('Raw browser prompt detail')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Admin PIN')).toBeInTheDocument();
    expect(button).toBeEnabled();
    expect(fetch.mock.calls.some(([url]) => url === '/api/admin/passkey/login/complete')).toBe(false);

    // Opening the existing fallback does not itself send a code or grant access.
    fireEvent.click(screen.getByRole('button', { name: /Email me a sign-in code/i }));
    expect(screen.getByLabelText('Authorized email')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url.includes('/email-code/'))).toBe(false);
  });

  it('clears old guidance while retrying and lets the next prompt finish independently', async () => {
    getCredential.mockRejectedValueOnce(new DOMException('Timed out', 'NotAllowedError'));
    let cancelRetry;
    getCredential.mockImplementationOnce(() => new Promise((resolve) => { cancelRetry = resolve; }));
    const button = await openPasskeyLogin();
    fireEvent.click(button);
    await screen.findByRole('alert');

    fireEvent.click(button);
    await waitFor(() => expect(getCredential).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(button).toBeDisabled();
    cancelRetry(null);
    expect(await screen.findByRole('alert')).toHaveTextContent('Passkey login cancelled');
    expect(button).toBeEnabled();
    expect(fetch.mock.calls.some(([url]) => url === '/api/admin/passkey/login/complete')).toBe(false);
  });

  it('keeps a server refusal distinct from a browser prompt interruption', async () => {
    const baseFetch = fetch.getMockImplementation();
    fetch.mockImplementation(async (url, options) => url === '/api/admin/passkey/login/begin'
      ? { ok: false, json: async () => ({ error: 'Sign-in is temporarily unavailable.' }) }
      : baseFetch(url, options));
    fireEvent.click(await openPasskeyLogin());
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign-in is temporarily unavailable.');
    expect(getCredential).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Admin PIN')).toBeInTheDocument();
  });
});
