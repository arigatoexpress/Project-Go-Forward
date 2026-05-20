import React from 'react';

// Single source of truth for status colors used by the CRM lead/deal pipeline
// and the chat history session list. Hex values stay here so they remain
// available to non-pill consumers (lead avatar background, pipeline column
// border, kanban dot) without having to dig into Tailwind config.

export const STATUS_COLORS = {
  new: '#3b82f6',
  contacted: '#f59e0b',
  qualified: '#8b5cf6',
  converted: '#22c55e',
};

export const DEAL_STATUS_COLORS = {
  pending: '#94a3b8',
  approved: '#3b82f6',
  contract: '#f59e0b',
  funded: '#8b5cf6',
  complete: '#22c55e',
  denied: '#ef4444',
  archived: '#6b7280',
};

// Tailwind utility chains for the soft-tinted appointment / chat-session pills
// where we don't want a saturated solid background. Keeping them as static
// strings (rather than computed) ensures Tailwind's content scanner picks them
// up at build time.
const APPT_STATUS_CLASSES = {
  confirmed: 'bg-green-100 text-green-800',
  completed: 'bg-blue-100 text-blue-800',
  cancelled: 'bg-red-100 text-red-800',
  no_show: 'bg-amber-100 text-amber-800',
};

const SESSION_STATUS_CLASSES = {
  active: 'bg-blue-100 text-blue-700',
  converted: 'bg-green-100 text-green-700',
  closed: 'bg-gray-100 text-gray-700',
};

const HOME_STATUS_CLASSES = {
  Available: 'bg-green-100 text-green-900 border border-green-300',
  Orderable: 'bg-blue-100 text-blue-900 border border-blue-300',
  'Orderable floorplan': 'bg-blue-100 text-blue-900 border border-blue-300',
  'Pre-Owned': 'bg-amber-100 text-amber-950 border border-amber-300',
  New: 'bg-green-100 text-green-900 border border-green-300', // Alias for Available
};

const SIZE_CLASSES = {
  sm: 'px-2 py-0.5 text-[11px]',
  md: 'px-3 py-1 text-xs',
};

const FALLBACK_HEX = '#94a3b8'; // slate-400

/**
 * StatusBadge — pill component that renders lead, deal, appointment, or chat
 * session statuses. Color source-of-truth lives in this module; CRM.jsx and
 * ChatHistory.jsx import from here.
 *
 * Props:
 *   status: string  — the raw status key (e.g. "new", "approved", "no_show")
 *   kind:   "lead" | "deal" | "appt" | "session"  (default "lead")
 *   size:   "sm" | "md"  (default "sm")
 */
export default function StatusBadge({ status, kind = 'lead', size = 'sm', className = '' }) {
  const base = `inline-flex items-center rounded-full font-semibold capitalize whitespace-nowrap ${SIZE_CLASSES[size] || SIZE_CLASSES.sm}`;

  if (kind === 'lead') {
    const bg = STATUS_COLORS[status] || FALLBACK_HEX;
    return (
      <span className={`${base} text-white ${className}`} style={{ background: bg }}>
        {status}
      </span>
    );
  }

  if (kind === 'deal') {
    const bg = DEAL_STATUS_COLORS[status] || FALLBACK_HEX;
    return (
      <span className={`${base} text-white ${className}`} style={{ background: bg }}>
        {status}
      </span>
    );
  }

  if (kind === 'appt') {
    const cls = APPT_STATUS_CLASSES[status] || 'bg-gray-100 text-gray-700';
    return <span className={`${base} ${cls} ${className}`}>{status}</span>;
  }

  if (kind === 'session') {
    const cls = SESSION_STATUS_CLASSES[status] || 'bg-gray-100 text-gray-700';
    return <span className={`${base} ${cls} ${className}`}>{status}</span>;
  }

  if (kind === 'home') {
    const cls = HOME_STATUS_CLASSES[status] || 'bg-gray-500 text-white';
    const label = status === 'Available' ? 'New' : status;
    return <span className={`${base} ${cls} ${className}`}>{label}</span>;
  }

  return <span className={`${base} bg-gray-200 text-gray-700 ${className}`}>{status}</span>;
}
