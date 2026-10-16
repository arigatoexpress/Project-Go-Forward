import React, { useEffect, useRef } from 'react';

// Plain-language yes/no dialog. The safe choice ("cancelLabel") gets focus so
// an accidental Enter never confirms a destructive action.
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Yes',
  cancelLabel = 'Cancel',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}) {
  const cancelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    cancelRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onCancel?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/60 p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="tho-confirm-title"
        aria-describedby={message ? 'tho-confirm-message' : undefined}
        className="w-full max-w-md rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-6 text-[var(--cp-text)] shadow-xl"
      >
        <h2 id="tho-confirm-title" className="text-lg font-semibold leading-snug">{title}</h2>
        {message && (
          <p id="tho-confirm-message" className="mt-2 text-sm text-[var(--cp-muted)] leading-relaxed">{message}</p>
        )}
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-[var(--cp-border)] px-4 py-2.5 text-sm font-medium hover:bg-[var(--cp-surface)] disabled:opacity-60"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`rounded-lg px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60 ${
              danger ? 'bg-red-700 hover:bg-red-800' : 'bg-[var(--cp-accent)] hover:opacity-90'
            }`}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
