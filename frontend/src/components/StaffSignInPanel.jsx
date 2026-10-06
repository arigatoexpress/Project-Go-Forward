import { useEffect, useState } from 'react';
import { Fingerprint, Loader2, Lock, Mail, ShieldCheck } from 'lucide-react';
import { extractErrorMessage, safeUserMessage } from '../utils/apiError';

const GENERIC_NOTICE =
  'If that email is on the team list, a message is on its way. Open it and tap Sign me in, or type the 6-digit code.';

const PIN_MAX = 64;

function readLinkFromHash() {
  if (typeof window === 'undefined') return null;
  const raw = window.location.hash.replace(/^#/, '');
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const token = params.get('t') || '';
  const email = (params.get('e') || '').trim().toLowerCase();
  if (!token || !email.includes('@')) return null;
  return { token, email };
}

function clearSignInHash() {
  if (typeof window === 'undefined') return;
  const next = `${window.location.pathname}${window.location.search}`;
  window.history.replaceState(window.history.state, '', next);
}

export default function StaffSignInPanel({
  onSuccess,
  onCancel,
  passkeyAvailable = false,
  passkeyStatus = null,
  passkeyLoading = false,
  passkeyError = '',
  onPasskeyLogin,
  initialNotice = '',
}) {
  const linked = readLinkFromHash();
  const [email, setEmail] = useState(linked?.email || '');
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [emailReady, setEmailReady] = useState(true);
  const [showPin, setShowPin] = useState(false);
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [linkToken, setLinkToken] = useState(linked?.token || '');

  useEffect(() => {
    let cancelled = false;
    fetch('/api/admin/sign-in/options', { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled && data && data.email_ready === false) {
          setEmailReady(false);
          setShowPin(true);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const finish = () => {
    clearSignInHash();
    onSuccess?.();
  };

  const requestEmail = async (event) => {
    event?.preventDefault();
    const address = email.trim();
    if (!address) {
      setError('Enter your work email.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/admin/email-code/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ email: address }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 503) {
        setEmailReady(false);
        setShowPin(true);
        setError(safeUserMessage(extractErrorMessage(data), 'Email sign-in is not turned on yet. Use the backup PIN or ask the owner.'));
        return;
      }
      window.localStorage.setItem('tho_passkey_email', address);
      setSent(true);
      setNotice(GENERIC_NOTICE);
      setLinkToken('');
    } catch {
      setSent(true);
      setNotice(GENERIC_NOTICE);
    } finally {
      setLoading(false);
    }
  };

  const verify = async (event, tokenOverride) => {
    event?.preventDefault();
    const address = email.trim();
    const token = tokenOverride || '';
    const typed = code.trim();
    if (!token && !typed) {
      setError('Enter the 6-digit code from your email.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const body = token
        ? { email: address, link_token: token }
        : { email: address, code: typed };
      const response = await fetch('/api/admin/email-code/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.success) {
        finish();
        return;
      }
      setError(safeUserMessage(extractErrorMessage(data), 'Invalid or expired code.'));
      setCode('');
    } catch {
      setError('Unable to verify. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const submitPin = async (event) => {
    event.preventDefault();
    setLoading(true);
    setPinError('');
    try {
      const response = await fetch('/api/admin/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ pin: pin.trim() }),
      });
      const data = await response.json().catch(() => ({}));
      if (data.success) {
        finish();
        return;
      }
      setPinError(`${safeUserMessage(extractErrorMessage(data), 'That PIN did not work.')} Use the email sign-in above, or ask a teammate for help.`);
      setPin('');
    } catch {
      setPinError('Unable to verify the PIN. Use the email sign-in above.');
    } finally {
      setLoading(false);
    }
  };

  const showPasskey = passkeyAvailable && passkeyStatus?.has_keys;
  const offCanonical = typeof window !== 'undefined'
    && !['www.texashomeoutlet.com', 'localhost', '127.0.0.1'].includes(window.location.hostname);

  return (
    <div id="staff-sign-in">
      <div className="flex items-center justify-center mb-4">
        <div className="p-3 bg-[var(--cp-accent-dim)] rounded-full">
          <Lock size={24} className="text-[var(--cp-accent)]" />
        </div>
      </div>
      <h2 className="text-xl font-bold text-center text-[var(--cp-text)] mb-1">Staff sign-in</h2>
      <p className="text-sm text-[var(--cp-muted)] text-center mb-5">
        Enter your work email. We will send a sign-in button to that inbox.
      </p>

      {initialNotice && (
        <p role="status" className="text-sm text-center mb-4">{initialNotice}</p>
      )}

      {offCanonical && (
        <p className="text-xs text-center mb-4 leading-relaxed">
          Bookmark{' '}
          <a className="underline" href="https://www.texashomeoutlet.com/staff">
            www.texashomeoutlet.com/staff
          </a>
          . Sign-in is most reliable on that page.
        </p>
      )}

      {!emailReady && (
        <p role="status" className="text-sm text-center mb-4 leading-relaxed">
          Email sign-in is not turned on yet. Ask the owner to finish email setup, or use the backup PIN.
        </p>
      )}

      {linkToken ? (
        <form onSubmit={(event) => verify(event, linkToken)} className="space-y-3">
          <p className="text-sm text-center break-all">
            Signing in as <span>{email}</span>
          </p>
          <button
            type="submit"
            disabled={loading}
            className="cp-btn-accent w-full py-3 rounded-lg text-base"
          >
            {loading ? 'Signing in...' : 'Sign me in'}
          </button>
          <button
            type="button"
            className="w-full py-2 text-xs text-[var(--cp-faint)]"
            onClick={() => { setLinkToken(''); setSent(true); }}
          >
            Type the code instead
          </button>
        </form>
      ) : !sent ? (
        <form onSubmit={requestEmail} className="space-y-3">
          <label className="block">
            <span className="sr-only">Work email</span>
            <input
              type="email"
              autoComplete="username"
              inputMode="email"
              value={email}
              onChange={(event) => { setEmail(event.target.value); setError(''); }}
              placeholder="name@texashomeoutlet.com"
              className="cp-input w-full px-3 py-3 text-base"
              aria-label="Work email"
              autoFocus
            />
          </label>
          <button
            type="submit"
            disabled={loading || !email.trim() || !emailReady}
            className="cp-btn-accent w-full py-3 rounded-lg text-base flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
            {loading ? 'Sending...' : 'Email me a sign-in link'}
          </button>
        </form>
      ) : (
        <form onSubmit={(event) => verify(event)} className="space-y-3">
          <p className="text-sm text-center break-all">
            Address entered: <span>{email.trim()}</span>
          </p>
          {notice && <p className="text-xs text-center leading-relaxed">{notice}</p>}
          <label className="block">
            <span className="sr-only">Sign-in code</span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(event) => { setCode(event.target.value.replace(/\D/g, '').slice(0, 6)); setError(''); }}
              placeholder="6-digit code"
              className="cp-input w-full px-3 py-3 text-center text-lg tracking-[0.3em]"
              aria-label="Sign-in code"
              autoFocus
            />
          </label>
          <button
            type="submit"
            disabled={loading || !code.trim()}
            className="cp-btn-accent w-full py-3 text-base flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
            {loading ? 'Verifying...' : 'Verify'}
          </button>
          <button
            type="button"
            onClick={() => { setSent(false); setCode(''); setError(''); setNotice(''); }}
            className="cp-btn-outline w-full py-2 text-sm"
          >
            Change email
          </button>
          <button
            type="button"
            onClick={requestEmail}
            disabled={loading}
            className="w-full py-1.5 text-xs text-[var(--cp-faint)]"
          >
            Resend code
          </button>
        </form>
      )}

      {error && (
        <p role="alert" className="text-[var(--cp-danger)] text-xs text-center mt-3">{error}</p>
      )}

      {showPasskey && (
        <div className="mt-4">
          <button
            type="button"
            onClick={onPasskeyLogin}
            disabled={passkeyLoading}
            aria-describedby={passkeyError ? 'passkey-login-error' : undefined}
            className="cp-btn-outline w-full py-2.5 text-sm flex items-center justify-center gap-2"
          >
            <Fingerprint size={16} />
            {passkeyLoading ? 'Authenticating...' : 'Sign in with Passkey'}
          </button>
        </div>
      )}
      {passkeyError && (
        <p id="passkey-login-error" role="alert" className="text-[var(--cp-danger)] text-xs text-center mt-2">
          {passkeyError}
        </p>
      )}

      <div className="mt-4">
        {!showPin ? (
          <button
            type="button"
            onClick={() => { setShowPin(true); setError(''); }}
            className="w-full py-2 text-sm text-[var(--cp-muted)] underline"
          >
            Use backup PIN
          </button>
        ) : (
          <form onSubmit={submitPin} className="space-y-2 border border-[var(--cp-border)] rounded-lg p-3">
            <p className="text-xs text-center text-[var(--cp-muted)]">Backup PIN</p>
            <input
              type="password"
              inputMode="text"
              maxLength={PIN_MAX}
              autoComplete="current-password"
              value={pin}
              onChange={(event) => { setPin(event.target.value.slice(0, PIN_MAX)); setPinError(''); }}
              placeholder="Enter admin PIN"
              className="cp-input w-full px-4 py-3 text-center text-lg tracking-[0.15em]"
              aria-label="Admin PIN"
            />
            {pinError && (
              <p className="text-[var(--cp-danger)] text-xs text-center">{pinError}</p>
            )}
            <button
              type="submit"
              disabled={!pin.trim() || loading}
              className="cp-btn-outline w-full py-3 text-sm"
            >
              {loading ? 'Verifying...' : 'Unlock'}
            </button>
          </form>
        )}
      </div>

      <p className="mt-4 text-xs text-[var(--cp-muted)] leading-relaxed text-center">
        Nothing arrived? Check spam. The message expires in 10 minutes. If it still does not come, ask a teammate who can sign in to add your email on the Team page. Do not send a code or PIN to anyone.
      </p>

      {onCancel && (
        <button
          type="button"
          onClick={onCancel}
          className="w-full mt-3 py-2 text-[var(--cp-faint)] text-xs"
        >
          Cancel
        </button>
      )}
    </div>
  );
}
