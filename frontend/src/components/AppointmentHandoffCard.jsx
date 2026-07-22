import React from 'react';
import { Check, CalendarDays, MapPin, ArrowRight } from 'lucide-react';

const ROUTE_STEPS = [
  { label: 'Request received', icon: Check, complete: true },
  { label: 'Choose a time', icon: CalendarDays },
  { label: 'Visit showroom', icon: MapPin },
];

export default function AppointmentHandoffCard({
  title,
  description,
  onStart,
  onContinue,
  continueLabel = 'Keep browsing',
}) {
  return (
    <div className="w-full max-w-xl text-left">
      <div className="mb-5 text-center">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--cp-cta-green)] text-white shadow-sm">
          <Check size={24} strokeWidth={3} aria-hidden="true" />
        </div>
        <p className="mb-1 text-[11px] font-bold uppercase tracking-[0.18em] text-[var(--cp-accent)]">
          Your showroom route
        </p>
        <h2 className="text-2xl font-bold text-[var(--cp-text)]">{title}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[var(--cp-muted)]">{description}</p>
      </div>

      <div className="relative mb-5 rounded-xl border border-[var(--cp-border)] bg-[var(--cp-bg-2)] px-2 py-4">
        <div className="absolute left-[17%] right-[17%] top-8 h-px bg-[var(--cp-copper)]" aria-hidden="true" />
        <ol className="grid grid-cols-3" aria-label="Showroom appointment progress">
          {ROUTE_STEPS.map((step, index) => (
            <li key={step.label} className="relative z-10 flex flex-col items-center px-1 text-center">
              <span className={`flex h-8 w-8 items-center justify-center rounded-full border text-xs font-bold ${step.complete ? 'border-[var(--cp-cta-green)] bg-[var(--cp-cta-green)] text-white' : index === 1 ? 'border-[var(--cp-accent)] bg-[var(--cp-panel)] text-[var(--cp-accent)]' : 'border-[var(--cp-copper)] bg-[var(--cp-panel)] text-[var(--cp-secondary)]'}`}>
                {React.createElement(step.icon, { size: 15, 'aria-hidden': true })}
              </span>
              <span className="mt-2 text-[10px] font-semibold leading-tight text-[var(--cp-text)] sm:text-xs">{step.label}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={onStart}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-[var(--cp-accent)] px-5 py-3 text-sm font-bold text-white shadow-sm transition hover:bg-[var(--cp-accent-hot)] focus:outline-none focus:ring-2 focus:ring-[var(--cp-accent)] focus:ring-offset-2"
        >
          Choose a visit time <ArrowRight size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={onContinue}
          className="rounded-lg border border-[var(--cp-border)] bg-[var(--cp-panel)] px-5 py-3 text-sm font-semibold text-[var(--cp-secondary)] transition hover:border-[var(--cp-copper)] hover:bg-[var(--cp-bg-2)] focus:outline-none focus:ring-2 focus:ring-[var(--cp-secondary)] focus:ring-offset-2"
        >
          {continueLabel}
        </button>
      </div>
    </div>
  );
}
