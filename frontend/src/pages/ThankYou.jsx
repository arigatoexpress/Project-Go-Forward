import React, { useEffect } from 'react';
import { BUSINESS_PHONE, BUSINESS_PHONE_RAW } from '../constants';
import { consumeThankYouConversion, trackEvent } from '../utils/analytics';
import AppointmentHandoffCard from '../components/AppointmentHandoffCard';

export default function ThankYou({ handoff, onHome, onBookAppointment }) {
  useEffect(() => {
    consumeThankYouConversion();
  }, []);

  const startVisit = () => {
    if (!handoff) return;
    trackEvent('appointment_handoff_started', {
      source: handoff.source || 'thank_you',
      intent: handoff.intent || 'contact',
      ...(handoff.home ? { home: handoff.home } : {}),
      ...(handoff.homeId ? { home_id: handoff.homeId } : {}),
    });
    onBookAppointment?.(handoff);
  };

  return (
    <main className="mx-auto max-w-4xl px-4 py-14 sm:px-6 sm:py-20">
      <h1 className="sr-only">Thank you</h1>
      {handoff ? (
        <div className="flex justify-center rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-6 shadow-xl sm:p-8">
          <AppointmentHandoffCard
            title="Message received"
            description="We received your request. You can also reserve a showroom time now."
            onStart={startVisit}
            onContinue={onHome}
            continueLabel="Return home"
          />
        </div>
      ) : (
        <div className="rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-6 text-center shadow-xl sm:p-8">
          <h2 className="text-2xl font-bold text-[var(--cp-text)]">Thank you</h2>
          <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[var(--cp-muted)]">
            We received your request. A teammate at Texas Home Outlet will follow up.
          </p>
          <p className="mt-6">
            <a
              href={`tel:${BUSINESS_PHONE_RAW}`}
              className="text-lg font-semibold text-[var(--cp-accent)] hover:underline"
            >
              Call {BUSINESS_PHONE}
            </a>
          </p>
          <button type="button" onClick={onHome} className="cp-btn-accent mt-6 px-5 py-2 text-sm">
            Back to homes
          </button>
        </div>
      )}
    </main>
  );
}
