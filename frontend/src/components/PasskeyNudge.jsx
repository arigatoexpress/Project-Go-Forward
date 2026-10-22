export default function PasskeyNudge({ onAdd, onLater, onNever }) {
  return (
    <div
      className="fixed inset-0 bg-black/70 z-[120] flex items-end sm:items-center justify-center p-4 backdrop-blur-sm"
      style={{ animation: 'tho-fade-in 0.15s ease' }}
    >
      <div
        role="dialog"
        aria-labelledby="passkey-nudge-title"
        className="cp-panel p-6 sm:p-7 max-w-md w-full"
        style={{ animation: 'tho-slide-up 0.2s ease' }}
      >
        <h2 id="passkey-nudge-title" className="text-2xl font-bold text-[var(--cp-text)]">
          Add a passkey for faster sign-in next time?
        </h2>
        <p className="text-base text-[var(--cp-muted)] mt-2 leading-relaxed">
          Next time you can use your fingerprint, face, or screen lock. The email link stays available.
        </p>
        <div className="mt-5 flex flex-col gap-2">
          <button type="button" onClick={onAdd} className="cp-btn-accent min-h-12 px-4 py-3 text-base">
            Add a passkey
          </button>
          <button type="button" onClick={onLater} className="cp-btn-outline min-h-12 px-4 py-3 text-base">
            Not now
          </button>
          <button type="button" onClick={onNever} className="min-h-12 px-4 py-3 text-base underline">
            Don’t ask again
          </button>
        </div>
      </div>
    </div>
  );
}
