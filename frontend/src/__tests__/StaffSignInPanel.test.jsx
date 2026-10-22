import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import StaffSignInPanel from '../components/StaffSignInPanel';

function renderPanel(props = {}) {
  return render(
    <StaffSignInPanel
      passkeyAvailable
      onPasskeyLogin={() => {}}
      {...props}
    />,
  );
}

describe('Staff sign-in panel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/admin/sign-in/options') {
        return { ok: true, json: async () => ({ email_ready: true }) };
      }
      return { ok: true, json: async () => ({}) };
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows Sign in with a passkey when the browser supports WebAuthn', async () => {
    renderPanel();

    const email = screen.getByRole('button', { name: /Email me a sign-in link/i });
    const passkey = screen.getByRole('button', { name: /Sign in with a passkey/i });
    const pin = screen.getByRole('button', { name: /Use backup PIN/i });

    expect(email.className).toContain('cp-btn-accent');
    expect(passkey.className).toContain('cp-btn-outline');
    expect(email.compareDocumentPosition(passkey) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(passkey.compareDocumentPosition(pin) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByLabelText('Work email')).toHaveAttribute('autocomplete', 'username webauthn');
    expect(screen.queryByText(/can’t save a passkey/i)).not.toBeInTheDocument();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/admin/sign-in/options',
        expect.objectContaining({ credentials: 'same-origin' }),
      );
    });
  });

  it('hides the passkey button on a browser that cannot use one, without an alarm', () => {
    renderPanel({ passkeyAvailable: false });

    expect(screen.getByRole('button', { name: /Email me a sign-in link/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sign in with a passkey/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/can’t save a passkey/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not supported/i)).not.toBeInTheDocument();
  });

  it('does not store the typed email for a later passkey prompt', async () => {
    renderPanel();
    expect(window.localStorage.getItem('tho_passkey_email')).toBeNull();
  });
});
