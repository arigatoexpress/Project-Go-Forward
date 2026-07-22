import React, { useEffect, useMemo, useState } from 'react';
import { ArchiveRestore, CalendarClock, Check, Mail, Phone, TimerReset } from 'lucide-react';

const RESPONSE_TARGET_MINUTES = 15;
const OVERDUE_MINUTES = 60;
const ACTIVE_WINDOW_MINUTES = 24 * 60;

const RESPONSE_STATES = {
  fresh: {
    label: 'Fresh',
    railClass: 'bg-emerald-500',
    badgeClass: 'bg-emerald-50 text-emerald-800 border-emerald-200',
    rank: 0,
  },
  waiting: {
    label: 'Waiting',
    railClass: 'bg-amber-400',
    badgeClass: 'bg-amber-50 text-amber-900 border-amber-200',
    rank: 1,
  },
  overdue: {
    label: 'Overdue',
    railClass: 'bg-rose-500',
    badgeClass: 'bg-rose-50 text-rose-800 border-rose-200',
    rank: 2,
  },
};

export function parseLeadCreatedAt(value) {
  const timestamp = String(value || '').trim();
  if (!timestamp) return new Date(Number.NaN);
  const hasTimezone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(timestamp);
  return new Date(hasTimezone ? timestamp : `${timestamp}Z`);
}

function ageMinutes(lead, now = new Date()) {
  const created = parseLeadCreatedAt(lead?.created_at);
  if (Number.isNaN(created.getTime())) return null;
  return Math.max(0, Math.floor((now.getTime() - created.getTime()) / 60000));
}

function waitingLabel(minutes) {
  if (minutes === null) return 'Time unknown';
  if (minutes < 1) return 'Just arrived';
  if (minutes < 60) return `${minutes}m waiting`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h waiting`;
  return `${Math.floor(minutes / 1440)}d waiting`;
}

export function getLeadResponseState(lead, now = new Date()) {
  const minutes = ageMinutes(lead, now);
  const key = minutes === null || minutes > OVERDUE_MINUTES
    ? 'overdue'
    : minutes > RESPONSE_TARGET_MINUTES
      ? 'waiting'
      : 'fresh';
  return {
    key,
    minutes,
    waitingLabel: waitingLabel(minutes),
    ...RESPONSE_STATES[key],
  };
}

function intentRank(lead) {
  if (lead.appointment_requested) return 2;
  if (lead.triage_reason === 'callback_requested') return 1;
  return 0;
}

function sortActiveLeads(a, b) {
  const intentDifference = intentRank(b) - intentRank(a);
  if (intentDifference !== 0) return intentDifference;
  if (a.responseState.rank !== b.responseState.rank) {
    return b.responseState.rank - a.responseState.rank;
  }
  const aAge = a.responseState.minutes ?? Number.MAX_SAFE_INTEGER;
  const bAge = b.responseState.minutes ?? Number.MAX_SAFE_INTEGER;
  return bAge - aAge;
}

function sortRecoveryLeads(a, b) {
  const aAge = a.responseState.minutes ?? Number.MAX_SAFE_INTEGER;
  const bAge = b.responseState.minutes ?? Number.MAX_SAFE_INTEGER;
  return aAge - bAge;
}

export function selectResponseQueues(leads, now = new Date()) {
  const reachable = (leads || [])
    .filter((lead) => lead?.status === 'new' && (lead.phone || lead.email))
    .map((lead) => ({ ...lead, responseState: getLeadResponseState(lead, now) }));

  return {
    active: reachable
      .filter((lead) => lead.responseState.minutes !== null
        && lead.responseState.minutes <= ACTIVE_WINDOW_MINUTES)
      .sort(sortActiveLeads),
    recovery: reachable
      .filter((lead) => lead.responseState.minutes === null
        || lead.responseState.minutes > ACTIVE_WINDOW_MINUTES)
      .sort(sortRecoveryLeads),
  };
}

export function selectResponseQueue(leads, now = new Date()) {
  const { active, recovery } = selectResponseQueues(leads, now);
  return [...active, ...recovery];
}

function intentLabel(lead) {
  if (lead.appointment_requested) return 'Appointment requested';
  if (lead.triage_reason === 'callback_requested') return 'Requested a callback';
  if (lead.financing_discussed) return 'Asked about financing';
  if ((lead.homes_viewed || []).length > 0) {
    const count = lead.homes_viewed.length;
    return `${count} home${count === 1 ? '' : 's'} viewed`;
  }
  return String(lead.source || 'website').replaceAll('_', ' ');
}

function LeadRow({ lead, busy, onOpen, onMarkContacted }) {
  const state = lead.responseState;
  return (
    <article className="relative px-4 py-4 sm:px-5">
      <span
        aria-hidden="true"
        className={`absolute inset-y-0 left-0 w-1 ${state.railClass}`}
      />
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => onOpen?.(lead)}
              className="truncate border-0 bg-transparent p-0 text-left text-sm font-bold text-slate-900 hover:text-blue-700 hover:underline"
            >
              {lead.name || 'Unknown lead'}
            </button>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${state.badgeClass}`}>
              {state.label} · {state.waitingLabel}
            </span>
            {lead.appointment_requested && (
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-800">
                <CalendarClock size={11} aria-hidden="true" /> Appointment
              </span>
            )}
            {!lead.appointment_requested && lead.triage_reason === 'callback_requested' && (
              <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-800">
                <Phone size={11} aria-hidden="true" /> Callback
              </span>
            )}
          </div>
          <p className="mt-1.5 mb-0 text-xs capitalize text-slate-600">{intentLabel(lead)}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {lead.phone && (
            <a
              href={`tel:${lead.phone}`}
              aria-label={`Call ${lead.name || 'lead'}`}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white no-underline shadow-sm hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              <Phone size={14} aria-hidden="true" /> Call
            </a>
          )}
          {lead.email && (
            <a
              href={`mailto:${lead.email}`}
              aria-label={`Email ${lead.name || 'lead'}`}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 no-underline hover:border-slate-400 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              <Mail size={14} aria-hidden="true" /> Email
            </a>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => onMarkContacted(lead)}
            aria-label={`Mark ${lead.name || 'lead'} contacted`}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-800 hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
          >
            <Check size={14} aria-hidden="true" /> {busy ? 'Saving…' : 'Contacted'}
          </button>
        </div>
      </div>
    </article>
  );
}

export default function LeadResponseQueue({ leads, onOpen, onMarkContacted }) {
  const [updatingId, setUpdatingId] = useState('');
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);
  const { active, recovery } = useMemo(() => selectResponseQueues(leads, now), [leads, now]);

  if (active.length === 0 && recovery.length === 0) return null;

  const markContacted = async (lead) => {
    setUpdatingId(lead.lead_id);
    try {
      await onMarkContacted(lead.lead_id);
    } finally {
      setUpdatingId('');
    }
  };

  return (
    <section
      aria-labelledby="lead-response-queue-title"
      className="my-4 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_12px_36px_-24px_rgba(15,23,42,0.65)]"
    >
      <div className="bg-slate-900 px-4 py-4 text-white sm:px-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10 text-amber-300">
              <TimerReset size={21} aria-hidden="true" />
            </span>
            <div>
              <h2 id="lead-response-queue-title" className="m-0 text-base font-bold tracking-tight">
                Respond now
              </h2>
              <p className="mt-1 mb-0 text-xs leading-5 text-slate-300">
                {active.length} lead{active.length === 1 ? '' : 's'} needs a response now · 15-minute target
              </p>
            </div>
          </div>
          <div className="grid grid-cols-3 overflow-hidden rounded-lg border border-white/10 text-[10px] font-semibold uppercase tracking-[0.12em]">
            <span className="bg-emerald-500/20 px-3 py-2 text-emerald-200">0–15 fresh</span>
            <span className="bg-amber-400/20 px-3 py-2 text-amber-200">16–60 wait</span>
            <span className="bg-rose-500/20 px-3 py-2 text-rose-200">61+ overdue</span>
          </div>
        </div>
      </div>

      <div data-testid="active-response-queue" className="divide-y divide-slate-100">
        {active.length > 0 ? active.slice(0, 6).map((lead) => (
          <LeadRow
            key={lead.lead_id}
            lead={lead}
            busy={updatingId === lead.lead_id}
            onOpen={onOpen}
            onMarkContacted={markContacted}
          />
        )) : (
          <p className="m-0 px-5 py-5 text-sm font-medium text-slate-600">
            No reachable leads arrived in the last 24 hours.
          </p>
        )}
      </div>
      {active.length > 6 && (
        <div className="border-t border-slate-100 bg-slate-50 px-5 py-2.5 text-xs font-medium text-slate-600">
          +{active.length - 6} more recent lead{active.length - 6 === 1 ? '' : 's'} in the list below
        </div>
      )}

      {recovery.length > 0 && (
        <div className="border-t-4 border-slate-200 bg-slate-50">
          <div className="flex flex-col gap-2 border-b border-slate-200 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="flex items-center gap-2">
              <ArchiveRestore size={17} className="text-slate-500" aria-hidden="true" />
              <h3 className="m-0 text-sm font-bold text-slate-800">Recovery backlog</h3>
            </div>
            <p className="m-0 text-xs font-medium text-slate-500">
              {recovery.length} older reachable lead{recovery.length === 1 ? '' : 's'} · over 24 hours · newest first
            </p>
          </div>
          <div data-testid="recovery-lead-queue" className="divide-y divide-slate-200 bg-white/70">
            {recovery.slice(0, 6).map((lead) => (
              <LeadRow
                key={lead.lead_id}
                lead={lead}
                busy={updatingId === lead.lead_id}
                onOpen={onOpen}
                onMarkContacted={markContacted}
              />
            ))}
          </div>
          {recovery.length > 6 && (
            <div className="border-t border-slate-200 px-5 py-2.5 text-xs font-medium text-slate-600">
              +{recovery.length - 6} more recovery lead{recovery.length - 6 === 1 ? '' : 's'} in the list below
            </div>
          )}
        </div>
      )}
    </section>
  );
}
