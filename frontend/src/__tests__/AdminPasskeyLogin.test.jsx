import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';
import { ToastProvider } from '../components/Toast';
import { PASSKEY_EMAIL_FALLBACK, PASSKEY_OLD_SITE } from '../auth/passkey';

const CLOSED = `${PASSKEY_EMAIL_FALLBACK} The passkey prompt was closed before it finished.`;
const TIMED_OUT = `${PASSKEY_EMAIL_FALLBACK} The passkey prompt timed out.`;
const ALREADY = `${PASSKEY_EMAIL_FALLBACK} This passkey is already saved for Texas Home Outlet. Try signing in with it.`;
let getCredential;

async function openPasskeyLogin() {
  render(<ToastProvider><App /></ToastProvider>);
  fireEvent.click(screen.getByRole('button', { name: /Admin access/i }));
  return screen.findByRole('button', { name: /Sign in with a passkey/i });
}

describe('Admin passkey sign-in recovery', () => {
  beforeEach(() => {
    vi.stubGlobal('PublicKeyCredential', class {});
    getCredential = vi.fn();
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: { get: getCredential, create: vi.fn() },
    });
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/admin/check') {
        return { ok: true, json: async () => ({ valid: false }) };
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
    window.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete navigator.credentials;
  });

  it.each(['NotAllowedError', 'AbortError'])('offers the email link after %s without granting access', async (name) => {
    getCredential.mockRejectedValue(new DOMException('Raw browser prompt detail', name));
    const button = await openPasskeyLogin();
    fireEvent.click(button);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(CLOSED);
    expect(alert).not.toHaveTextContent(/pin/i);
    expect(button).toHaveAccessibleDescription(CLOSED);
    expect(button.parentElement.nextElementSibling).toBe(alert);
    expect(screen.queryByText('Raw browser prompt detail')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Use backup PIN/i }));
    expect(screen.getByLabelText('Backup PIN')).toBeInTheDocument();
    expect(button).toBeEnabled();
    expect(fetch.mock.calls.some(([url]) => url === '/api/admin/passkey/login/complete')).toBe(false);
    expect(screen.getByLabelText('Work email')).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/email-code/'))).toBe(false);
  });

  it('explains a timeout separately and still leads with the email link', async () => {
    getCredential.mockRejectedValue(new DOMException('The operation timed out', 'TimeoutError'));
    fireEvent.click(await openPasskeyLogin());
    expect(await screen.findByRole('alert')).toHaveTextContent(TIMED_OUT);
  });

  it('treats InvalidStateError as an already registered passkey', async () => {
    getCredential.mockRejectedValue(new DOMException('duplicate', 'InvalidStateError'));
    fireEvent.click(await openPasskeyLogin());
    expect(await screen.findByRole('alert')).toHaveTextContent(ALREADY);
  });

  it('tells people old-site passkeys do not carry over on SecurityError', async () => {
    getCredential.mockRejectedValue(new DOMException('bad rp', 'SecurityError'));
    fireEvent.click(await openPasskeyLogin());
    expect(await screen.findByRole('alert')).toHaveTextContent(PASSKEY_OLD_SITE);
  });

  it('uses the old-site sentence when the server does not recognize the key', async () => {
    const baseFetch = fetch.getMockImplementation();
    fetch.mockImplementation(async (url, options) => {
      if (url === '/api/admin/passkey/login/complete') {
        return { ok: false, json: async () => ({ detail: 'Unknown credential' }) };
      }
      return baseFetch(url, options);
    });
    getCredential.mockResolvedValue({
      id: 'cred',
      rawId: new Uint8Array([1, 2, 3]).buffer,
      type: 'public-key',
      response: {
        authenticatorData: new Uint8Array([4]).buffer,
        clientDataJSON: new Uint8Array([5]).buffer,
        signature: new Uint8Array([6]).buffer,
        userHandle: null,
      },
    });
    fireEvent.click(await openPasskeyLogin());
    expect(await screen.findByRole('alert')).toHaveTextContent(PASSKEY_OLD_SITE);
  });

  it('clears old guidance while retrying and lets the next prompt finish independently', async () => {
    getCredential.mockRejectedValueOnce(new DOMException('Raw browser prompt detail', 'NotAllowedError'));
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
    expect(await screen.findByRole('alert')).toHaveTextContent(CLOSED);
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
    fireEvent.click(screen.getByRole('button', { name: /Use backup PIN/i }));
    expect(screen.getByLabelText('Backup PIN')).toBeInTheDocument();
  });

  it('does not alarm the sign-in page when this browser cannot use a passkey', async () => {
    vi.stubGlobal('PublicKeyCredential', undefined);
    delete window.PublicKeyCredential;
    render(<ToastProvider><App /></ToastProvider>);
    fireEvent.click(screen.getByRole('button', { name: /Admin access/i }));
    expect(await screen.findByRole('button', { name: /Email me a sign-in link/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sign in with a passkey/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/can’t save a passkey/i)).not.toBeInTheDocument();
  });
});
