import { KeyRound, Loader2 } from 'lucide-react';

export default function AddPasskeyDialog({
  open,
  phase,
  email = '',
  otherOwnerEmails = [],
  error = '',
  tried = false,
  loading = false,
  onAdd,
  onTry,
  onClose,
}) {
  if (!open) return null;
  const title = phase === 'saved'
    ? 'Passkey saved'
    : phase === 'confirm'
      ? 'Add a passkey'
      : phase === 'working'
        ? 'Add a passkey'
        : 'Passkey';
  return (
    <div
      className="fixed inset-0 bg-black/70 z-[110] flex items-center justify-center p-4 backdrop-blur-sm"
      style={{ animation: 'tho-fade-in 0.15s ease' }}
    >
      <div
        role="dialog"
        aria-labelledby="add-passkey-title"
        className="cp-panel p-6 sm:p-7 max-w-md w-full"
        style={{ animation: 'tho-slide-up 0.2s ease' }}
      >
        <div className="flex items-start gap-3 mb-4">
          <div className="p-2.5 bg-[var(--cp-accent-dim)] rounded-lg">
            <KeyRound size={20} className="text-[var(--cp-accent)]" />
          </div>
          <h2 id="add-passkey-title" className="text-2xl font-bold text-[var(--cp-text)]">{title}</h2>
        </div>

        {phase === 'confirm' && (
          <div className="space-y-4">
            <p className="text-base leading-relaxed">
              This passkey is saved for the email you are signed in with.
            </p>
            <label className="block">
              <span className="text-base font-semibold">Work email</span>
              <input
                type="email"
                value={email}
                readOnly
                className="cp-input w-full mt-1.5 px-3 py-3 text-base min-h-12"
                aria-label="Work email"
              />
            </label>
            {otherOwnerEmails.length > 0 && (
              <p className="text-sm text-[var(--cp-muted)] leading-relaxed">
                To add a passkey for {otherOwnerEmails.join(' or ')}, sign in with that email first.
              </p>
            )}
          </div>
        )}

        {phase === 'working' && (
          <p className="text-base leading-relaxed flex items-center gap-2">
            <Loader2 size={16} className="animate-spin" /> Follow the prompt on this device, your phone, or a security key.
          </p>
        )}

        {phase === 'saved' && (
          <div className="space-y-3">
            <p className="text-base leading-relaxed">
              Passkey saved{email ? ` for ${email}` : ''}. You are still on this page.
            </p>
            {tried && <p role="status" className="text-base">That passkey works.</p>}
          </div>
        )}

        {error && (
          <p role="alert" className="mt-4 text-base text-[var(--cp-danger)] leading-relaxed">{error}</p>
        )}

        <div className="mt-5 flex flex-col sm:flex-row gap-2 sm:justify-end">
          <button type="button" onClick={onClose} className="cp-btn-outline min-h-12 px-4 py-3 text-base" disabled={loading && phase === 'working'}>
            {phase === 'saved' ? 'Close' : 'Cancel'}
          </button>
          {phase === 'confirm' && (
            <button type="button" onClick={onAdd} className="cp-btn-accent min-h-12 px-4 py-3 text-base" disabled={loading}>
              Add a passkey
            </button>
          )}
          {phase === 'saved' && !tried && (
            <button type="button" onClick={onTry} className="cp-btn-accent min-h-12 px-4 py-3 text-base" disabled={loading}>
              {loading ? 'Checking...' : 'Try signing in with it now'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
