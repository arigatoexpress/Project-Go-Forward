import React, { useState } from 'react';
import { startAuthentication } from '@simplewebauthn/browser';
import { Fingerprint, Loader2 } from 'lucide-react';

/**
 * PasskeyLogin — "Sign in with passkey" button for the admin modal.
 *
 * Props:
 *   onSuccess(token: string) — called with the JWT when login succeeds
 *   onFallback()             — called when user clicks "Use PIN instead"
 */
export default function PasskeyLogin({ onSuccess, onFallback }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handlePasskeyLogin = async () => {
    setLoading(true);
    setError('');
    try {
      const beginRes = await fetch('/api/auth/webauthn/login/begin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 'admin' }),
      });
      if (!beginRes.ok) {
        throw new Error('Server error starting passkey login');
      }
      const { challenge_id, options } = await beginRes.json();

      // Prompt the browser / authenticator (TouchID / FaceID / Windows Hello / Proton Pass)
      const credential = await startAuthentication({ optionsJSON: options });

      const completeRes = await fetch('/api/auth/webauthn/login/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge_id, credential }),
      });
      const data = await completeRes.json();
      if (data.success && data.token) {
        onSuccess(data.token);
      } else {
        setError(data.detail || 'Passkey login failed. Try your PIN.');
      }
    } catch (err) {
      if (err?.name === 'NotAllowedError') {
        setError('Passkey prompt was cancelled or timed out.');
      } else if (err?.name === 'SecurityError') {
        setError('Passkeys are not available on this origin.');
      } else {
        setError('Passkey login failed. Use PIN instead.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="w-full">
      <button
        type="button"
        onClick={handlePasskeyLogin}
        disabled={loading}
        className="w-full flex items-center justify-center gap-2 py-3 bg-blue-600 text-white rounded-lg font-semibold text-base hover:bg-blue-700 active:bg-blue-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        aria-label="Sign in with passkey (TouchID, FaceID, Windows Hello, or security key)"
      >
        {loading
          ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
          : <Fingerprint className="h-5 w-5" aria-hidden="true" />}
        {loading ? 'Authenticating…' : 'Sign in with passkey'}
      </button>

      {error && (
        <p className="text-red-500 text-sm text-center mt-3" role="alert">
          {error}
        </p>
      )}

      <div className="relative my-4">
        <div className="absolute inset-0 flex items-center" aria-hidden="true">
          <div className="w-full border-t border-gray-200" />
        </div>
        <div className="relative flex justify-center text-sm">
          <span className="px-3 bg-white text-gray-400">or</span>
        </div>
      </div>

      <button
        type="button"
        onClick={onFallback}
        className="w-full py-2 text-sm text-gray-500 hover:text-gray-800 transition-colors"
      >
        Use PIN instead
      </button>
    </div>
  );
}
