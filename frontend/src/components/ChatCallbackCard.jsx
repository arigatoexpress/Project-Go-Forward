import React, { useEffect, useRef, useState } from 'react';
import { CalendarDays, CheckCircle2, Loader2, PhoneCall, X } from 'lucide-react';
import { getUtmParams } from '../utils/utm';
import { getJourneyAttribution } from '../utils/attribution';
import { trackEvent } from '../utils/analytics';

export default function ChatCallbackCard({ sessionId, captured = false, onCaptured, onDismiss }) {
  const cardRef = useRef(null);
  const [form, setForm] = useState({ name: '', phone: '', email: '' });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(captured);
  const [error, setError] = useState('');

  useEffect(() => {
    if (captured || typeof window === 'undefined' || window.innerWidth > 640) return undefined;
    const frame = window.requestAnimationFrame(() => {
      cardRef.current?.scrollIntoView({
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [captured]);

  const submit = async (event) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setError('Enter your name so the specialist knows who to ask for.');
      return;
    }
    const phoneDigits = form.phone.replace(/\D/g, '');
    if (!(phoneDigits.length === 10 || (phoneDigits.length === 11 && phoneDigits.startsWith('1')))) {
      setError('Enter a valid 10-digit phone number.');
      return;
    }

    setSubmitting(true);
    setError('');
    try {
      const response = await fetch('/api/chat/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId,
          name: form.name.trim(),
          phone: form.phone.trim(),
          email: form.email.trim() || undefined,
          consent: true,
          ...getUtmParams(),
          ...getJourneyAttribution(),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !body.success) {
        setError(body.error || 'Could not save your callback request. Please call the showroom.');
        return;
      }
      setSubmitted(true);
      trackEvent('lead_captured', { source: 'chat_callback' });
      onCaptured?.(body.lead_id);
    } catch {
      setError('Could not save your callback request. Please call the showroom.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <aside ref={cardRef} className="relative ml-12 mr-2 scroll-mt-3 overflow-hidden rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface)] shadow-sm" aria-label="Request a callback">
      <span className="absolute inset-y-0 left-0 w-1 bg-[var(--cp-accent)]" aria-hidden="true" />
      <div className="px-4 py-4 pl-5 sm:px-5 sm:pl-6">
        {submitted ? (
          <div className="flex items-start gap-3" role="status">
            <CheckCircle2 className="mt-0.5 shrink-0 text-emerald-500" size={22} aria-hidden="true" />
            <div>
              <p className="m-0 text-sm font-bold text-[var(--cp-text)]">Your callback request is saved.</p>
              <p className="mt-1 mb-0 text-xs leading-5 text-[var(--cp-muted)]">A Texas Home Outlet specialist can pick up where this chat left off.</p>
            </div>
          </div>
        ) : (
          <>
            <button
              type="button"
              onClick={onDismiss}
              aria-label="Dismiss callback form"
              className="absolute right-2 top-2 rounded-md p-1.5 text-[var(--cp-faint)] hover:bg-[var(--cp-panel)] hover:text-[var(--cp-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--cp-accent)]"
            >
              <X size={15} aria-hidden="true" />
            </button>
            <div className="mb-3 flex items-start gap-3 pr-7">
              <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--cp-accent-dim)] text-[var(--cp-accent)]">
                <PhoneCall size={18} aria-hidden="true" />
              </span>
              <div>
                <p className="m-0 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-[var(--cp-accent)]">Personal follow-up</p>
                <h2 className="mt-1 mb-0 text-sm font-bold text-[var(--cp-text)]">Want a home specialist to call you?</h2>
                <p className="mt-1 mb-0 text-xs leading-5 text-[var(--cp-muted)]">Leave your name and number. They can continue from this conversation.</p>
              </div>
            </div>

            <form onSubmit={submit} className="grid gap-2 sm:grid-cols-2">
              <label className="block">
                <span className="sr-only">Your name</span>
                <input
                  aria-label="Your name"
                  autoComplete="name"
                  value={form.name}
                  onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                  className="cp-input w-full px-3 py-2.5 text-sm"
                  placeholder="Your name"
                  maxLength={120}
                  required
                />
              </label>
              <label className="block">
                <span className="sr-only">Best callback number</span>
                <input
                  aria-label="Best callback number"
                  type="tel"
                  inputMode="tel"
                  autoComplete="tel"
                  value={form.phone}
                  onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))}
                  className="cp-input w-full px-3 py-2.5 text-sm"
                  placeholder="Best callback number"
                  maxLength={40}
                  required
                />
              </label>
              <label className="block sm:col-span-2">
                <span className="sr-only">Email (optional)</span>
                <input
                  aria-label="Email (optional)"
                  type="email"
                  autoComplete="email"
                  value={form.email}
                  onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
                  className="cp-input w-full px-3 py-2.5 text-sm"
                  placeholder="Email (optional)"
                  maxLength={254}
                />
              </label>
              {error && (
                <p className="m-0 rounded-md border border-[var(--cp-danger)]/25 bg-[var(--cp-danger-dim)] px-3 py-2 text-xs text-[var(--cp-danger)] sm:col-span-2" role="alert">{error}</p>
              )}
              <div className="flex flex-col gap-2 pt-1 sm:col-span-2 sm:flex-row sm:items-center">
                <button
                  type="submit"
                  disabled={submitting}
                  className="cp-btn-accent inline-flex min-h-10 items-center justify-center gap-2 px-4 py-2 text-sm font-bold disabled:cursor-wait disabled:opacity-60"
                >
                  {submitting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <PhoneCall size={15} aria-hidden="true" />}
                  {submitting ? 'Saving…' : 'Request my callback'}
                </button>
                <a href="/appointments" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md px-3 py-2 text-xs font-semibold text-[var(--cp-accent)] hover:bg-[var(--cp-accent-dim)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--cp-accent)]">
                  <CalendarDays size={14} aria-hidden="true" /> Book a showroom visit
                </a>
              </div>
              <p className="m-0 text-[10px] leading-4 text-[var(--cp-faint)] sm:col-span-2">By requesting a callback, you agree Texas Home Outlet may call or text about this inquiry. Consent is not required to purchase.</p>
            </form>
          </>
        )}
      </div>
    </aside>
  );
}
