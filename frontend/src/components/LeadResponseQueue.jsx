import React, { useEffect, useMemo, useState } from 'react';
import { ArchiveRestore, CalendarClock, Check, Mail, Phone, TimerReset } from 'lucide-react';
import { leadInterestLine } from '../utils/leadInterest';

const RESPONSE_TARGET_MINUTES = 15;
const OVERDUE_MINUTES = 60;
const ACTIVE_WINDOW_MINUTES = 24 * 60;

const RESPONSE_STATES = {
  fresh: {
    label: 'New',
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
    label: 'Needs a call',
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
  if (lead.appointment_requested) return 'They want to visit';
  if (lead.triage_reason === 'callback_requested') return 'They asked for a call back';
  if (lead.financing_discussed) return 'They asked about financing';
  if (lead.home_label || lead.home_model) return `Asked about ${lead.home_label || lead.home_model}`;
  if ((lead.homes_viewed || []).length > 0) {
    const count = lead.homes_viewed.length;
    return `Looked at ${count} home${count === 1 ? '' : 's'}`;
  }
  return 'Asked from the website';
}

export function starterReply(lead) {
  const home = lead?.home_label || lead?.home_model || (lead?.homes_viewed || [])[0];
  const about = home ? ` about ${home}` : '';
  return `Thank you for contacting Texas Home Outlet${about}. A teammate can help with the home, the price, and a visit to the lot.\n\nReply to this email or call the store and we will help from here.`;
}

function LeadRow({
  lead, busy, answering, reply, sending, onOpen, onMarkContacted, onStartAnswer, onReplyChange, onSendReply,
}) {
  const state = lead.responseState;
  const who = lead.name || 'this person';
  return (
    <article className="relative px-4 py-4 sm:px-5">
      <span
        aria-hidden="true"
        className={`absolute inset-y-0 left-0 w-1 ${state.railClass}`}
      />
      <div className="flex flex-col gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => onOpen?.(lead)}
              className="min-h-12 truncate border-0 bg-transparent px-1 text-left text-lg font-bold text-slate-900 hover:text-blue-700 hover:underline"
            >
              {lead.name || 'Unknown person'}
            </button>
            <span className={`rounded-full border px-3 py-1 text-sm font-bold ${state.badgeClass}`}>
              {state.label} · {state.waitingLabel}
            </span>
            {lead.appointment_requested && (
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-3 py-1 text-sm font-semibold text-violet-800">
                <CalendarClock size={16} aria-hidden="true" /> Wants a visit
              </span>
            )}
            {!lead.appointment_requested && lead.triage_reason === 'callback_requested' && (
              <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-3 py-1 text-sm font-semibold text-blue-800">
                <Phone size={16} aria-hidden="true" /> Call them back
              </span>
            )}
          </div>
          <p className="mt-1 mb-0 text-base text-slate-700">{intentLabel(lead)}</p>
          <p className="mt-1 mb-0 text-base font-semibold text-slate-800">{leadInterestLine(lead)}</p>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {lead.phone && (
            <a
              href={`tel:${lead.phone}`}
              aria-label={`Call ${lead.name || 'lead'}`}
              className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 text-base font-bold text-white no-underline shadow-sm hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              <Phone size={18} aria-hidden="true" /> Call
            </a>
          )}
          {lead.email && (
            <a
              href={`mailto:${lead.email}`}
              aria-label={`Email ${lead.name || 'lead'}`}
              className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-base font-bold text-slate-800 no-underline hover:bg-slate-50"
            >
              <Mail size={18} aria-hidden="true" /> Email
            </a>
          )}
          {lead.email && (
            <button
              type="button"
              onClick={() => onStartAnswer(lead)}
              aria-expanded={answering}
              aria-label={`Write ${who} a reply`}
              className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border border-blue-300 bg-blue-50 px-4 text-base font-bold text-blue-900 hover:bg-blue-100"
            >
              {answering ? 'Reply is open' : 'Write a reply'}
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => onMarkContacted(lead)}
            aria-label={`Mark ${lead.name || 'lead'} contacted`}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border border-emerald-300 bg-emerald-50 px-4 text-base font-bold text-emerald-800 hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
          >
            <Check size={18} aria-hidden="true" /> {busy ? 'Saving…' : 'I already talked to them'}
          </button>
        </div>

        {answering && lead.email && (
          <form
            className="rounded-xl border border-slate-200 bg-slate-50 p-4"
            onSubmit={(event) => { event.preventDefault(); onSendReply(lead); }}
          >
            <p className="m-0 text-base leading-relaxed text-slate-700">
              This note goes to {lead.email}. You can change the words, then send it from here.
            </p>
            <label className="mt-3 block text-base font-semibold text-slate-800" htmlFor={`reply-${lead.lead_id}`}>
              Your reply
              <textarea
                id={`reply-${lead.lead_id}`}
                value={reply}
                onChange={(event) => onReplyChange(event.target.value)}
                rows={6}
                required
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-3 text-base leading-relaxed text-slate-900"
              />
            </label>
            <button
              type="submit"
              disabled={sending || !reply.trim()}
              className="mt-3 inline-flex min-h-12 items-center justify-center rounded-lg bg-blue-600 px-4 text-base font-bold text-white disabled:opacity-60"
            >
              {sending ? 'Sending…' : 'Send this reply'}
            </button>
          </form>
        )}
      </div>
    </article>
  );
}

export default function LeadResponseQueue({ leads, onOpen, onMarkContacted, onSendReply }) {
  const [updatingId, setUpdatingId] = useState('');
  const [answerId, setAnswerId] = useState('');
  const [reply, setReply] = useState('');
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');
  const [answerError, setAnswerError] = useState('');
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);
  const { active, recovery } = useMemo(() => selectResponseQueues(leads, now), [leads, now]);

  const queueEmpty = active.length === 0 && recovery.length === 0;
  if (queueEmpty && !notice && !answerError) return null;

  const markContacted = async (lead) => {
    setUpdatingId(lead.lead_id);
    setAnswerError('');
    setNotice('');
    try {
      const saved = await onMarkContacted(lead.lead_id);
      if (saved === false) {
        setAnswerError('That was not saved. This person is still waiting. Try again.');
        return;
      }
      setNotice(`Saved. ${lead.name || 'This person'} is marked as answered.`);
      if (answerId === lead.lead_id) setAnswerId('');
    } catch {
      setAnswerError('That was not saved. This person is still waiting. Check the connection and try again.');
    } finally {
      setUpdatingId('');
    }
  };

  const sendReply = async (lead) => {
    if (!reply.trim()) {
      setAnswerError('Type a short note before sending.');
      return;
    }
    if (!onSendReply) {
      setAnswerError('Replies cannot be sent from here yet. Use the Email button.');
      return;
    }
    setSending(true);
    setAnswerError('');
    setNotice('');
    try {
      const sent = await onSendReply(lead, {
        subject: 'Following up from Texas Home Outlet',
        message: reply.trim(),
      });
      if (sent === false) {
        setAnswerError('The reply did not send. Nothing was marked answered. Try again, or call them.');
        return;
      }
      const saved = await onMarkContacted?.(lead.lead_id);
      if (saved === false) {
        setNotice('The reply was sent, but the list still shows them as waiting. Tap “I already talked to them”.');
        return;
      }
      setNotice(`Sent. ${lead.name || 'They'} has your reply, and this person is marked answered.`);
      setAnswerId('');
    } catch {
      setAnswerError('The reply did not send. Nothing was marked answered. Try again, or call them.');
    } finally {
      setSending(false);
    }
  };

  const startAnswer = (lead) => {
    setAnswerError('');
    setNotice('');
    if (answerId === lead.lead_id) {
      setAnswerId('');
      return;
    }
    setAnswerId(lead.lead_id);
    setReply(starterReply(lead));
  };

  const rowProps = {
    onOpen,
    onMarkContacted: markContacted,
    onStartAnswer: startAnswer,
    onReplyChange: setReply,
    onSendReply: sendReply,
    reply,
    sending,
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
              <h2 id="lead-response-queue-title" className="m-0 text-xl font-bold tracking-tight">
                Answer these people
              </h2>
              <p className="mt-1 mb-0 text-base leading-6 text-slate-200">
                {queueEmpty
                  ? 'You are caught up.'
                  : `${active.length} ${active.length === 1 ? 'person is' : 'people are'} waiting for an answer. Try to answer within 15 minutes.`}
              </p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-1 text-sm font-semibold sm:grid-cols-3">
            <span className="rounded-lg bg-emerald-500/20 px-3 py-2 text-emerald-100">New: first 15 minutes</span>
            <span className="rounded-lg bg-amber-400/20 px-3 py-2 text-amber-100">Waiting: up to an hour</span>
            <span className="rounded-lg bg-rose-500/20 px-3 py-2 text-rose-100">Needs a call: over an hour</span>
          </div>
        </div>
      </div>

      {(notice || answerError) && (
        <p
          role={answerError ? 'alert' : 'status'}
          className={`m-0 px-5 py-3 text-base leading-relaxed ${answerError ? 'bg-rose-50 text-rose-800' : 'bg-emerald-50 text-emerald-900'}`}
        >
          {answerError || notice}
        </p>
      )}

      <div data-testid="active-response-queue" className="divide-y divide-slate-100">
        {queueEmpty ? (
          <p className="m-0 px-5 py-5 text-base font-medium text-slate-700">
            Nobody else is waiting right now.
          </p>
        ) : active.length > 0 ? active.slice(0, 6).map((lead) => (
          <LeadRow
            key={lead.lead_id}
            lead={lead}
            busy={updatingId === lead.lead_id}
            answering={answerId === lead.lead_id}
            {...rowProps}
          />
        )) : (
          <p className="m-0 px-5 py-5 text-base font-medium text-slate-700">
            Nobody new in the last 24 hours has a phone number or email.
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
              <h3 className="m-0 text-lg font-bold text-slate-800">Older messages</h3>
            </div>
            <p className="m-0 text-base font-medium text-slate-600">
              {recovery.length} older {recovery.length === 1 ? 'person' : 'people'} you can still reach, newest first
            </p>
          </div>
          <div data-testid="recovery-lead-queue" className="divide-y divide-slate-200 bg-white/70">
            {recovery.slice(0, 6).map((lead) => (
              <LeadRow
                key={lead.lead_id}
                lead={lead}
                busy={updatingId === lead.lead_id}
                answering={answerId === lead.lead_id}
                {...rowProps}
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
