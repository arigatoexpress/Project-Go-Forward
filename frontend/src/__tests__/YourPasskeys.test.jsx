import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import YourPasskeys from '../pages/YourPasskeys';

const KEY = {
  credential_id: 'abc',
  label: 'Front desk iPhone',
  provider: 'iCloud Keychain',
  created_at: '2026-10-01T12:00:00Z',
  last_used_at: '2026-10-20T12:00:00Z',
};

describe('Your passkeys', () => {
  beforeEach(() => {
    vi.stubGlobal('PublicKeyCredential', {
      isUserVerifyingPlatformAuthenticatorAvailable: vi.fn(async () => true),
      isConditionalMediationAvailable: vi.fn(async () => false),
    });
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (String(url).includes('/credentials/mine') && (!options.method || options.method === 'GET')) {
        return { ok: true, json: async () => ({ email: 'pat@texashomeoutlet.com', credentials: [KEY] }) };
      }
      return { ok: true, json: async () => ({ success: true, credential: { ...KEY, label: 'Office phone' } }) };
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the device name, dates, rename, and remove', async () => {
    render(<YourPasskeys onAdd={() => {}} />);
    expect(await screen.findByRole('heading', { name: 'Your passkeys' })).toBeInTheDocument();
    expect(screen.getByText('Front desk iPhone')).toBeInTheDocument();
    expect(screen.getByText('iCloud Keychain')).toBeInTheDocument();
    expect(screen.getByText(/Added/)).toBeInTheDocument();
    expect(screen.getByText(/Last used/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add a passkey/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Rename/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Remove/i })).toBeInTheDocument();
  });

  it('says this device can’t save a passkey when there is no platform authenticator', async () => {
    window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = vi.fn(async () => false);
    render(<YourPasskeys onAdd={() => {}} />);
    expect(await screen.findByText('This device can’t save a passkey. Use the email link.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add a passkey/i })).not.toBeInTheDocument();
  });

  it('renames a key from the page', async () => {
    render(<YourPasskeys onAdd={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /Rename/i }));
    const input = screen.getByLabelText('Passkey name');
    fireEvent.change(input, { target: { value: 'Office phone' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const patch = fetch.mock.calls.find(([, options]) => options?.method === 'PATCH');
    expect(patch[0]).toContain('/api/admin/passkey/credentials/mine/abc');
    expect(JSON.parse(patch[1].body)).toEqual({ label: 'Office phone' });
  });
});
