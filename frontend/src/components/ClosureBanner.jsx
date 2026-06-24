import React, { useState } from 'react';
import { X, CalendarOff } from 'lucide-react';
import { STORE_CLOSURE } from '../constants';

// localStorage key for per-browser dismissal. We store the closure's END date
// so a NEW closure window (different end date) re-shows even if a prior one was
// dismissed — staff never have to ask visitors to "clear their cache".
const STORAGE_KEY = 'tho-closure-dismissed';

// Parse an ISO `YYYY-MM-DD` string into a local Date at midnight. Using the
// explicit constructor (not `new Date('YYYY-MM-DD')`, which parses as UTC)
// keeps the comparison in the visitor's local day.
function parseLocalDate(iso) {
  if (!iso || typeof iso !== 'string') return null;
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

/**
 * Site-wide store-closure banner.
 *
 * Renders ONLY when:
 *   - `closure` is non-null, AND
 *   - today (`now`) is on or before the closure's END date, AND
 *   - this browser has not dismissed THIS closure window.
 *
 * `now` is injectable so the banner is deterministic in tests (no real-clock
 * time-bomb). `closure` defaults to STORE_CLOSURE from constants.js so staff
 * edit dates/copy there without touching this component.
 */
export default function ClosureBanner({ closure = STORE_CLOSURE, now = new Date() }) {
  const endDate = closure ? parseLocalDate(closure.end) : null;

  // Dismissal is keyed to the active end date so a later closure re-appears.
  const initiallyDismissed = (() => {
    if (!closure) return false;
    try {
      return window.localStorage.getItem(STORAGE_KEY) === closure.end;
    } catch {
      return false; // private mode / storage disabled — show the banner
    }
  })();

  const [dismissed, setDismissed] = useState(initiallyDismissed);

  if (!closure || !endDate) return null;

  // End date is inclusive: compare against the end-of-day moment so the final
  // closed day still shows the banner. Hide once we're past it.
  const endOfClosure = new Date(
    endDate.getFullYear(), endDate.getMonth(), endDate.getDate(), 23, 59, 59, 999,
  );
  if (now.getTime() > endOfClosure.getTime()) return null;

  if (dismissed) return null;

  const handleDismiss = () => {
    setDismissed(true);
    try {
      window.localStorage.setItem(STORAGE_KEY, closure.end);
    } catch {
      /* storage unavailable — dismissal just won't persist across reloads */
    }
  };

  return (
    <div
      role="status"
      className="tho-closure-banner bg-[var(--cp-accent-hot)] text-[var(--cp-bg)] border-b border-[var(--cp-accent)]"
    >
      <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2 flex items-center justify-center gap-2.5 text-xs sm:text-sm font-semibold text-center">
        <CalendarOff size={15} className="shrink-0" aria-hidden="true" />
        <span>{closure.message}</span>
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Dismiss closure notice"
          className="tho-closure-dismiss absolute right-3 sm:right-5 inline-flex items-center justify-center p-1 rounded hover:bg-[var(--cp-accent)]/40 focus:outline-none focus:ring-2 focus:ring-[var(--cp-bg)]/60 transition-colors"
        >
          <X size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
