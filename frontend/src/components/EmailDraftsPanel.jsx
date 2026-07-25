import React, { useState, useEffect, useCallback } from 'react';
import { Inbox, ChevronRight, ChevronDown, Lock, RefreshCw } from 'lucide-react';
import adminFetch from '../adminFetch';
import { safeUserMessage, extractErrorMessage } from '../utils/apiError';

// Draft lifecycle statuses from GET /api/admin/email-reply-drafts.
// Approve/reject decisions happen exclusively in the Telegram gate — this
// panel is strictly read-only.
const DRAFT_STATUSES = ['pending', 'approved', 'rejected', 'sent', 'expired'];

// Soft-pill classes mirror the appointment/session pill convention in
// StatusBadge.jsx. Kept as static strings so Tailwind's content scanner
// picks them up at build time.
const DRAFT_STATUS_CLASSES = {
  pending: 'bg-amber-100 text-amber-800',
  approved: 'bg-blue-100 text-blue-800',
  rejected: 'bg-red-100 text-red-800',
  sent: 'bg-green-100 text-green-800',
  expired: 'bg-gray-100 text-gray-700',
};

function DraftStatusPill({ status }) {
  const cls = DRAFT_STATUS_CLASSES[status] || 'bg-gray-100 text-gray-700';
  return (
    <span className={`inline-flex items-center rounded-full font-semibold capitalize whitespace-nowrap px-2 py-0.5 text-[11px] ${cls}`}>
      {status}
    </span>
  );
}

/**
 * EmailDraftsPanel — read-only list of AI-drafted replies to inbound emails
 * awaiting (or past) human review. Review decisions happen in the Telegram
 * gate; this surface exists so staff can see what was drafted and how each
 * draft was decided. Data: GET /api/admin/email-reply-drafts.
 *
 * Fetching is lazy: CRM.jsx only mounts this panel while the "Reply Drafts"
 * tab is active, and the list refetches whenever the status filter changes.
 *
 * Props:
 *   timeAgo: (iso: string) => string — relative-time helper from CRM.jsx
 */
export default function EmailDraftsPanel({ timeAgo }) {
  const [drafts, setDrafts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [expandedId, setExpandedId] = useState(null);

  const fetchDrafts = useCallback(async (status) => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (status) params.set('status', status);
      const res = await adminFetch(`/api/admin/email-reply-drafts?${params.toString()}`);
      const data = await res.json();
      if (data.success) {
        setDrafts(data.drafts || []);
      } else {
        setDrafts([]);
        setError(safeUserMessage(extractErrorMessage(data), 'Failed to load reply drafts.'));
      }
    } catch (err) {
      console.error('Reply drafts fetch failed:', err);
      setDrafts([]);
      setError('Failed to load reply drafts. Check connection and try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDrafts(statusFilter); }, [fetchDrafts, statusFilter]);

  return (
    <div>
      {/* Status filter chips — same pill style as the leads tab */}
      <div className="flex flex-wrap items-center gap-2 py-3">
        {['', ...DRAFT_STATUSES].map(s => (
          <button
            key={s}
            type="button"
            className={
              'px-3.5 py-1.5 text-xs rounded-full border transition-colors capitalize ' +
              (statusFilter === s
                ? 'border-blue-500 text-blue-700 bg-blue-50'
                : 'border-gray-300 text-gray-700 bg-white hover:border-gray-400')
            }
            onClick={() => setStatusFilter(s)}
          >
            {s || 'All'}
          </button>
        ))}
        <button
          type="button"
          onClick={() => fetchDrafts(statusFilter)}
          className="ml-auto inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs rounded-full border border-gray-300 text-gray-700 bg-white hover:border-gray-400 transition-colors"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Read-only notice — decisions happen in the Telegram gate */}
      <div className="flex items-center gap-1.5 pb-3 text-xs text-gray-500">
        <Lock size={12} className="shrink-0" />
        Drafts are reviewed via the Telegram gate — this list is read-only.
      </div>

      {loading && <div className="text-center py-12 text-gray-700">Loading...</div>}

      {!loading && error && (
        <div role="alert" className="flex items-center justify-between gap-3 bg-red-50 border border-red-200 text-red-800 rounded-lg px-4 py-3 text-sm">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => fetchDrafts(statusFilter)}
            className="shrink-0 font-semibold underline hover:text-red-900"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && drafts.length === 0 && (
        <div className="text-center py-12 text-gray-600 text-sm">
          {statusFilter ? `No ${statusFilter} drafts` : 'No reply drafts yet'}
        </div>
      )}

      {/* Draft rows — styled after the Email Log tab rows, click to expand */}
      {!loading && !error && drafts.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {drafts.map(draft => {
            const expanded = expandedId === draft.draft_id;
            return (
              <div key={draft.draft_id} className="bg-white rounded-lg">
                <button
                  type="button"
                  aria-expanded={expanded}
                  className="flex items-center gap-3 w-full px-4 py-3.5 bg-transparent border-0 text-left cursor-pointer rounded-lg transition hover:shadow-sm"
                  onClick={() => setExpandedId(expanded ? null : draft.draft_id)}
                >
                  <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center text-blue-700 shrink-0">
                    <Inbox size={16} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-[13px] text-slate-800 truncate">
                      {draft.subject || '(no subject)'}
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-700 mt-0.5">
                      <span className="truncate">From: {draft.sender}</span>
                      {draft.triage_label && (
                        <span className="bg-gray-50 px-2 py-0.5 rounded text-[11px]">{draft.triage_label}</span>
                      )}
                      <DraftStatusPill status={draft.status} />
                    </div>
                  </div>
                  <div className="text-xs text-gray-600 shrink-0">{timeAgo(draft.created_at)}</div>
                  {expanded
                    ? <ChevronDown size={16} className="text-gray-400 shrink-0" />
                    : <ChevronRight size={16} className="text-gray-400 shrink-0" />}
                </button>

                {expanded && (
                  <div className="px-4 pb-4 pt-2 flex flex-col gap-3 border-t border-gray-100">
                    {Array.isArray(draft.rule_hits) && draft.rule_hits.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {draft.rule_hits.map(hit => (
                          <span key={hit} className="bg-gray-50 border border-gray-200 px-2 py-0.5 rounded text-[11px] text-gray-600">
                            {hit}
                          </span>
                        ))}
                      </div>
                    )}
                    <div>
                      <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Inbound excerpt
                      </div>
                      <div className="text-[13px] text-gray-700 leading-snug whitespace-pre-wrap bg-gray-50 rounded-lg px-3 py-2">
                        {draft.inbound_excerpt || '—'}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Reply draft
                      </div>
                      <div className="text-[13px] text-slate-800 leading-snug whitespace-pre-wrap bg-blue-50/50 border border-blue-100 rounded-lg px-3 py-2">
                        {draft.draft_body || '—'}
                      </div>
                    </div>
                    {(draft.decided_by || draft.updated_at || draft.lead_id || draft.message_id) && (
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500">
                        {draft.decided_by && <span>Decided by: {draft.decided_by}</span>}
                        {draft.updated_at && <span>Updated: {timeAgo(draft.updated_at)}</span>}
                        {draft.lead_id && <span>Lead: {draft.lead_id}</span>}
                        {draft.message_id && <span>Message: {draft.message_id}</span>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
