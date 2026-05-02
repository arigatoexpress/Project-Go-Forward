import React, { useState, useEffect, useCallback } from 'react';
import { startRegistration } from '@simplewebauthn/browser';
import { Key, Trash2, Plus, Loader2, Shield, X } from 'lucide-react';

/**
 * PasskeySettings — list, add, and revoke passkeys for the admin account.
 *
 * Props:
 *   adminToken: string  — current X-Admin-Token
 *   onClose(): void     — called when the panel should close
 */
export default function PasskeySettings({ adminToken, onClose }) {
  const [credentials, setCredentials] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newLabel, setNewLabel] = useState('');
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const authHeaders = {
    'X-Admin-Token': adminToken,
    'Content-Type': 'application/json',
  };

  const fetchCredentials = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/auth/webauthn/credentials', { headers: authHeaders });
      if (!res.ok) throw new Error('fetch failed');
      const data = await res.json();
      if (data.success) setCredentials(data.credentials);
    } catch {
      setError('Could not load passkeys.');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { fetchCredentials(); }, [fetchCredentials]);

  const handleRegister = async () => {
    if (!newLabel.trim()) {
      setError('Give your passkey a name, e.g. "Mark\'s iPhone".');
      return;
    }
    setRegistering(true);
    setError('');
    setMessage('');
    try {
      const beginRes = await fetch('/api/auth/webauthn/register/begin', {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ user_id: 'admin', label: newLabel.trim() }),
      });
      if (!beginRes.ok) throw new Error('Server error starting registration');
      const { challenge_id, options } = await beginRes.json();

      // Prompt the browser to create a new passkey
      const credential = await startRegistration({ optionsJSON: options });

      const completeRes = await fetch('/api/auth/webauthn/register/complete', {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ challenge_id, credential, label: newLabel.trim() }),
      });
      const data = await completeRes.json();
      if (data.success) {
        setMessage(`"${newLabel.trim()}" registered! You can now sign in with this device.`);
        setNewLabel('');
        await fetchCredentials();
      } else {
        setError(data.detail || 'Registration failed. Try again.');
      }
    } catch (err) {
      if (err?.name === 'NotAllowedError') {
        setError('Registration was cancelled.');
      } else if (err?.name === 'InvalidStateError') {
        setError('This device already has a passkey registered.');
      } else {
        setError('Failed to register passkey. Try again.');
      }
    } finally {
      setRegistering(false);
    }
  };

  const handleRevoke = async (credentialId, label) => {
    if (!window.confirm(`Revoke passkey "${label}"?\n\nYou won't be able to sign in with this device until you re-register.`)) {
      return;
    }
    setError('');
    setMessage('');
    try {
      const res = await fetch(`/api/auth/webauthn/credentials/${credentialId}`, {
        method: 'DELETE',
        headers: authHeaders,
      });
      const data = await res.json();
      if (data.success) {
        setMessage(`"${label}" revoked.`);
        await fetchCredentials();
      } else {
        setError(data.detail || 'Failed to revoke passkey.');
      }
    } catch {
      setError('Failed to revoke passkey.');
    }
  };

  const formatDate = (iso) => {
    if (!iso) return null;
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 z-[110] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Passkey settings"
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-blue-600" aria-hidden="true" />
            <h2 className="text-lg font-semibold text-gray-900">Passkeys</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-700 rounded transition-colors"
            aria-label="Close passkey settings"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="px-6 py-4">
          <p className="text-sm text-gray-500 mb-4">
            Sign in with TouchID, FaceID, Windows Hello, or a security key.
            Add a passkey from each device you use. Your PIN remains available as a backup.
          </p>

          {/* Registered passkeys */}
          {loading ? (
            <div className="flex justify-center py-6">
              <Loader2 className="h-5 w-5 animate-spin text-gray-300" aria-label="Loading passkeys" />
            </div>
          ) : credentials.length === 0 ? (
            <div className="text-center py-6 text-sm text-gray-400">
              No passkeys registered yet. Add one below to enable quick sign-in.
            </div>
          ) : (
            <ul className="space-y-2 mb-4" aria-label="Registered passkeys">
              {credentials.map((cred) => (
                <li
                  key={cred.credential_id}
                  className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <Key className="h-4 w-4 text-gray-400 flex-shrink-0" aria-hidden="true" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {cred.label}
                      </p>
                      <p className="text-xs text-gray-400">
                        Added {formatDate(cred.registered_at)}
                        {cred.last_used_at
                          ? ` · Last used ${formatDate(cred.last_used_at)}`
                          : ' · Never used'}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => handleRevoke(cred.credential_id, cred.label)}
                    className="ml-3 p-1.5 text-gray-400 hover:text-red-600 rounded transition-colors flex-shrink-0"
                    aria-label={`Revoke passkey: ${cred.label}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/* Add passkey */}
          <div className="border-t border-gray-100 pt-4">
            <p className="text-sm font-medium text-gray-700 mb-2">Add a passkey for this device</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={newLabel}
                onChange={(e) => { setNewLabel(e.target.value); setError(''); setMessage(''); }}
                onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
                placeholder="e.g. Mark's iPhone"
                maxLength={64}
                className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
                aria-label="Passkey device name"
                disabled={registering}
              />
              <button
                onClick={handleRegister}
                disabled={registering || !newLabel.trim()}
                className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 active:bg-blue-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                aria-label="Register new passkey"
              >
                {registering
                  ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  : <Plus className="h-4 w-4" aria-hidden="true" />}
                {registering ? 'Setting up…' : 'Add'}
              </button>
            </div>

            {error && (
              <p className="text-red-500 text-sm mt-2" role="alert">{error}</p>
            )}
            {message && (
              <p className="text-green-600 text-sm mt-2" role="status">{message}</p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 pb-5">
          <p className="text-xs text-gray-400">
            Lost all passkeys? Use your PIN to log in, then re-add passkeys here.
          </p>
        </div>
      </div>
    </div>
  );
}
