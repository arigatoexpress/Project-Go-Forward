import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import StaffSignInPanel from '../components/StaffSignInPanel';

function renderPanel() {
  return render(
    <StaffSignInPanel
      passkeyAvailable
      passkeyStatus={{ enabled: true, has_keys: true, store_ready: true }}
      onPasskeyLogin={() => {}}
    />,
  );
}

describe('Staff sign-in panel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/admin/sign-in/options') {
        return { ok: true, json: async () => ({ email_ready: true, passkey_sign_in: false }) };
      }
      return { ok: true, json: async () => ({}) };
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('hides passkey sign-in even when a key exists and the browser supports it', async () => {
    renderPanel();

    expect(screen.getByRole('button', { name: /Email me a sign-in link/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use backup PIN/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/admin/sign-in/options',
        expect.objectContaining({ credentials: 'same-origin' }),
      );
    });
    expect(screen.queryByRole('button', { name: /Sign in with Passkey/i })).not.toBeInTheDocument();
  });

  it('shows passkey only as a small link under the backup PIN when the owner flag is on', async () => {
    fetch.mockImplementation(async (url) => {
      if (url === '/api/admin/sign-in/options') {
        return { ok: true, json: async () => ({ email_ready: true, passkey_sign_in: true }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    renderPanel();

    const email = screen.getByRole('button', { name: /Email me a sign-in link/i });
    const pin = screen.getByRole('button', { name: /Use backup PIN/i });
    const passkey = await screen.findByRole('button', { name: /Sign in with Passkey/i });

    expect(email.className).toContain('cp-btn-accent');
    expect(email.className).toContain('w-full');
    expect(passkey.className).not.toContain('w-full');
    expect(passkey.className).not.toContain('cp-btn-accent');
    expect(pin.compareDocumentPosition(passkey) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps passkey hidden when the flag is on but no key is enrolled', async () => {
    fetch.mockImplementation(async (url) => {
      if (url === '/api/admin/sign-in/options') {
        return { ok: true, json: async () => ({ email_ready: true, passkey_sign_in: true }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(
      <StaffSignInPanel
        passkeyAvailable
        passkeyStatus={{ enabled: true, has_keys: false, store_ready: true }}
        onPasskeyLogin={() => {}}
      />,
    );

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(screen.queryByRole('button', { name: /Sign in with Passkey/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Email me a sign-in link/i })).toBeInTheDocument();
  });
});
