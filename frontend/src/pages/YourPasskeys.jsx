import { useCallback, useEffect, useRef, useState } from 'react';
import { KeyRound, Loader2, Pencil, Trash2 } from 'lucide-react';
import adminFetch from '../adminFetch';
import { detectPasskeySupport, PASSKEY_UNSUPPORTED_DEVICE } from '../auth/passkey';
import { describeFetchError, extractErrorMessage, safeUserMessage } from '../utils/apiError';

function titleFor(cred) {
  const label = String(cred?.label || '').trim();
  if (label) return label;
  return cred?.provider || 'Passkey';
}

function when(value, empty) {
  if (!value) return empty;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return empty;
  return date.toLocaleDateString();
}

export default function YourPasskeys({ onAdd, onBack, refreshToken = 0 }) {
  const [credentials, setCredentials] = useState([]);
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [platform, setPlatform] = useState(false);
  const [supportReady, setSupportReady] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [renameId, setRenameId] = useState('');
  const [renameValue, setRenameValue] = useState('');
  const loadGeneration = useRef(0);

  useEffect(() => {
    let cancelled = false;
    detectPasskeySupport().then((support) => {
      if (cancelled) return;
      setPlatform(Boolean(support.platform));
      setSupportReady(true);
    });
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setError('');
    try {
      const response = await adminFetch('/api/admin/passkey/credentials/mine', { credentials: 'same-origin' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(safeUserMessage(extractErrorMessage(data), 'Your passkeys could not be loaded.'));
      }
      if (generation !== loadGeneration.current) return;
      setEmail(data.email || '');
      setCredentials(Array.isArray(data.credentials) ? data.credentials : []);
    } catch (err) {
      if (generation !== loadGeneration.current) return;
      setError(safeUserMessage(err?.message, describeFetchError(err, 'load your passkeys')));
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load, refreshToken]);

  const rename = async (credentialId) => {
    const label = renameValue.trim();
    if (!label) return;
    setBusyId(credentialId);
    setError('');
    try {
      const response = await adminFetch(`/api/admin/passkey/credentials/mine/${encodeURIComponent(credentialId)}`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(safeUserMessage(extractErrorMessage(data), 'The name could not be saved.'));
      }
      setRenameId('');
      setRenameValue('');
      await load();
    } catch (err) {
      setError(safeUserMessage(err?.message, describeFetchError(err, 'rename the passkey')));
    } finally {
      setBusyId('');
    }
  };

  const remove = async (credentialId, name) => {
    if (!window.confirm(`Remove ${name}? You can add a passkey again later. Use the email link if this was your only one.`)) return;
    setBusyId(credentialId);
    setError('');
    try {
      const response = await adminFetch(`/api/admin/passkey/credentials/mine/${encodeURIComponent(credentialId)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(safeUserMessage(extractErrorMessage(data), 'The passkey could not be removed.'));
      }
      await load();
    } catch (err) {
      setError(safeUserMessage(err?.message, describeFetchError(err, 'remove the passkey')));
    } finally {
      setBusyId('');
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-8 text-[var(--cp-text)]">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold">Your passkeys</h1>
          <p className="text-lg text-[var(--cp-muted)] mt-2 leading-relaxed">
            {email
              ? `These sign you in as ${email}. The email link still works if a passkey does not.`
              : 'A passkey lets you sign in with this device, your phone, or a security key.'}
          </p>
        </div>
        {supportReady && platform && (
          <button type="button" onClick={onAdd} className="cp-btn-accent min-h-12 px-4 py-3 text-base inline-flex items-center gap-2">
            <KeyRound size={16} /> Add a passkey
          </button>
        )}
      </div>

      {supportReady && !platform && (
        <p role="status" className="mb-4 text-base leading-relaxed">{PASSKEY_UNSUPPORTED_DEVICE}</p>
      )}

      {error && (
        <p role="alert" className="mb-4 text-base text-[var(--cp-danger)] leading-relaxed">{error}</p>
      )}

      {loading ? (
        <p className="text-base flex items-center gap-2"><Loader2 size={16} className="animate-spin" /> Loading your passkeys...</p>
      ) : credentials.length === 0 ? (
        <p className="text-base leading-relaxed border border-[var(--cp-border)] rounded-lg px-4 py-3">
          You don’t have a passkey yet.
        </p>
      ) : (
        <ul className="space-y-3">
          {credentials.map((cred) => {
            const name = titleFor(cred);
            const editing = renameId === cred.credential_id;
            return (
              <li key={cred.credential_id} className="border border-[var(--cp-border)] rounded-lg px-4 py-3 bg-[var(--cp-panel)]">
                {editing ? (
                  <form
                    className="flex flex-col sm:flex-row gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      rename(cred.credential_id);
                    }}
                  >
                    <label className="sr-only" htmlFor={`rename-${cred.credential_id}`}>Passkey name</label>
                    <input
                      id={`rename-${cred.credential_id}`}
                      value={renameValue}
                      onChange={(event) => setRenameValue(event.target.value)}
                      className="cp-input flex-1 px-3 py-2 text-base min-h-12"
                      maxLength={60}
                    />
                    <button type="submit" className="cp-btn-accent min-h-12 px-4" disabled={busyId === cred.credential_id}>
                      Save
                    </button>
                    <button
                      type="button"
                      className="cp-btn-outline min-h-12 px-4"
                      onClick={() => { setRenameId(''); setRenameValue(''); }}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-lg font-semibold">{name}</p>
                      {cred.provider && cred.provider !== name && (
                        <p className="text-sm text-[var(--cp-muted)]">{cred.provider}</p>
                      )}
                      <p className="text-sm text-[var(--cp-muted)] mt-1">
                        Added {when(cred.created_at, 'unknown')}
                        {' · '}
                        {cred.last_used_at ? `Last used ${when(cred.last_used_at, 'unknown')}` : 'Not used yet'}
                      </p>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <button
                        type="button"
                        className="cp-btn-outline min-h-11 px-3 inline-flex items-center gap-1"
                        onClick={() => { setRenameId(cred.credential_id); setRenameValue(name); }}
                        disabled={Boolean(busyId)}
                      >
                        <Pencil size={14} /> Rename
                      </button>
                      <button
                        type="button"
                        className="cp-btn-outline min-h-11 px-3 inline-flex items-center gap-1 text-[var(--cp-danger)]"
                        onClick={() => remove(cred.credential_id, name)}
                        disabled={Boolean(busyId)}
                      >
                        {busyId === cred.credential_id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        Remove
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {onBack && (
        <button type="button" onClick={onBack} className="mt-6 underline text-base">
          Back
        </button>
      )}
    </div>
  );
}
