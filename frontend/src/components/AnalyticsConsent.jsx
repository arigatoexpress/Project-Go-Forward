import React, { useState } from 'react';
import { ShieldCheck } from 'lucide-react';

const CONSENT_KEY = 'tho_analytics_consent_v1';

function savedChoice() {
  try {
    const value = localStorage.getItem(CONSENT_KEY);
    return value === 'granted' || value === 'denied' ? value : null;
  } catch {
    return null;
  }
}

function saveChoice(value) {
  try {
    localStorage.setItem(CONSENT_KEY, value);
  } catch {
    // A blocked storage API must not break the storefront.
  }
}

export default function AnalyticsConsent() {
  const configured = typeof window !== 'undefined' && window.__THO_ANALYTICS_CONFIGURED__ === true;
  const [choice, setChoice] = useState(savedChoice);
  const [editing, setEditing] = useState(false);

  if (!configured) return null;

  const decide = (value) => {
    saveChoice(value);
    window.__THO_ANALYTICS_CONSENT__ = value;
    if (value === 'granted') {
      window.__THO_ENABLE_ANALYTICS__?.();
      // The initial React page-view happened before consent and was correctly
      // suppressed. Record the current public page once after the grant.
      window.gtag?.('event', 'page_view', { page_path: window.location.pathname });
    } else {
      window.__THO_DISABLE_ANALYTICS__?.();
    }
    setChoice(value);
    setEditing(false);
  };

  if (choice && !editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="fixed bottom-3 left-3 z-[140] rounded-full border border-[var(--cp-border)] bg-[var(--cp-panel)] px-3 py-1.5 text-[11px] font-semibold text-[var(--cp-muted)] shadow-md hover:text-[var(--cp-text)]"
      >
        Privacy choices
      </button>
    );
  }

  return (
    <section
      role="dialog"
      aria-label="Analytics privacy choices"
      aria-live="polite"
      className="fixed bottom-3 z-[140] max-w-3xl rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-4 text-[var(--cp-text)] shadow-2xl sm:p-5"
      style={{ left: '50%', transform: 'translateX(-50%)', width: 'calc(100% - 1.5rem)' }}
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-full bg-[var(--cp-accent-dim)] p-2 text-[var(--cp-accent)]">
          <ShieldCheck size={20} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-bold sm:text-base">Your privacy choices</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--cp-muted)] sm:text-sm">
            We use optional analytics and ad measurement to understand which homes and ads help shoppers. We never send your name,
            email, phone number, or message to analytics providers. The site works normally if you decline.
          </p>
          <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button type="button" onClick={() => decide('denied')} className="cp-btn-outline px-4 py-2 text-sm">
              No thanks
            </button>
            <button type="button" onClick={() => decide('granted')} className="cp-btn-accent px-4 py-2 text-sm">
              Allow analytics
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
