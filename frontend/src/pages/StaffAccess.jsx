import { useEffect, useState } from 'react';
import { Loader2, UserPlus } from 'lucide-react';
import adminFetch from '../adminFetch';
import { describeFetchError, extractErrorMessage, responseErrorMessage, safeUserMessage } from '../utils/apiError';

export default function StaffAccess({ onBack }) {
  const [directory, setDirectory] = useState(null);
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await adminFetch('/api/admin/staff', { credentials: 'same-origin' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) {
        throw new Error(await responseErrorMessage(response, { context: 'load the team list', body: data }));
      }
      setDirectory(data);
    } catch (err) {
      setError(safeUserMessage(err?.message, describeFetchError(err, 'load the team list')));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const update = async (address, action) => {
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const response = await adminFetch('/api/admin/staff', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: address, action }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) {
        throw new Error(safeUserMessage(
          extractErrorMessage(data),
          await responseErrorMessage(response, { context: 'update the team list', body: data }),
        ));
      }
      setEmail('');
      setNotice(action === 'block' ? 'That person can no longer sign in with email.' : 'Team list updated.');
      await load();
    } catch (err) {
      setError(safeUserMessage(err?.message, describeFetchError(err, 'update the team list')));
    } finally {
      setSaving(false);
    }
  };

  const domains = directory?.domains?.join(', ') || 'texashomeoutlet.com';

  return (
    <div className="max-w-xl mx-auto px-4 py-8">
      <button type="button" onClick={onBack} className="text-sm text-[var(--cp-muted)] mb-4">
        Back
      </button>
      <h1 className="text-2xl font-bold text-[var(--cp-text)] mb-2">Team access</h1>
      <p className="text-sm text-[var(--cp-muted)] leading-relaxed mb-6">
        People with an @{domains} email can sign in unless you block them. Add anyone else by email. Removing someone stops their email sign-in. It does not change the shared backup PIN.
      </p>

      {loading && !directory ? (
        <p className="text-sm flex items-center gap-2"><Loader2 size={16} className="animate-spin" /> Loading team list...</p>
      ) : (
        <>
          <form
            className="flex flex-col sm:flex-row gap-2 mb-6"
            onSubmit={(event) => { event.preventDefault(); update(email.trim(), 'allow'); }}
          >
            <label className="flex-1">
              <span className="sr-only">Email to add</span>
              <input
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                aria-label="Email to add"
                className="cp-input w-full px-3 py-3 text-base"
              />
            </label>
            <button type="submit" disabled={saving || !email.trim()} className="cp-btn-accent px-4 py-3 text-sm flex items-center justify-center gap-2">
              <UserPlus size={16} /> Add
            </button>
          </form>

          {notice && <p className="text-sm mb-3">{notice}</p>}
          {error && <p role="alert" className="text-sm text-[var(--cp-danger)] mb-3">{error}</p>}

          <section className="mb-6">
            <h2 className="font-semibold mb-2">Extra people</h2>
            {(directory?.added || []).length === 0 ? (
              <p className="text-sm text-[var(--cp-muted)]">No extra emails yet.</p>
            ) : (
              <ul className="space-y-2">
                {directory.added.map((address) => (
                  <li key={address} className="flex items-center justify-between gap-3 text-sm">
                    <span className="break-all">{address}</span>
                    <button type="button" className="cp-btn-outline px-3 py-1" disabled={saving} onClick={() => update(address, 'reset')}>
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mb-6">
            <h2 className="font-semibold mb-2">Blocked</h2>
            {(directory?.blocked || []).length === 0 ? (
              <p className="text-sm text-[var(--cp-muted)]">Nobody is blocked.</p>
            ) : (
              <ul className="space-y-2">
                {directory.blocked.map((address) => (
                  <li key={address} className="flex items-center justify-between gap-3 text-sm">
                    <span className="break-all">{address}</span>
                    <button type="button" className="cp-btn-outline px-3 py-1" disabled={saving} onClick={() => update(address, 'reset')}>
                      Unblock
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <form
            className="border border-[var(--cp-border)] rounded-lg p-3"
            onSubmit={(event) => {
              event.preventDefault();
              const form = event.currentTarget;
              const blocked = new FormData(form).get('blockEmail');
              update(String(blocked || '').trim(), 'block');
              form.reset();
            }}
          >
            <h2 className="font-semibold mb-2">Block an email</h2>
            <p className="text-xs text-[var(--cp-muted)] mb-2">Use this when someone with a company email should not sign in anymore.</p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                name="blockEmail"
                type="email"
                required
                placeholder="former@texashomeoutlet.com"
                aria-label="Email to block"
                className="cp-input flex-1 px-3 py-3 text-base"
              />
              <button type="submit" disabled={saving} className="cp-btn-outline px-4 py-3 text-sm">
                Block
              </button>
            </div>
          </form>
        </>
      )}
    </div>
  );
}
