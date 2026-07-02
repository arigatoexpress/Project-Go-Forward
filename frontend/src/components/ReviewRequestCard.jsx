import { useState } from 'react';
import { Copy, Mail, MessageSquare, Star } from 'lucide-react';
import {
  SALESPERSON_STORAGE_KEY,
  buildMailtoHref,
  buildReviewMessage,
  buildSmsHref,
} from '../reviewRequest';

const EMAIL_SUBJECT = 'How was your experience with Texas Home Outlet?';

function readStoredSalesperson() {
  try {
    return localStorage.getItem(SALESPERSON_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

/**
 * Review-request helper card. Builds a prefilled Google-review request the
 * staff member sends from their OWN phone/email client (sms:/mailto: links) —
 * nothing is sent by the app. Hidden entirely when no review link is
 * configured (GOOGLE_REVIEW_LINK unset).
 */
export default function ReviewRequestCard({ reviewLink, lead = null, onClose = null }) {
  const [salesperson, setSalesperson] = useState(readStoredSalesperson);
  const [customerName, setCustomerName] = useState(lead?.name || '');
  const [phone, setPhone] = useState(lead?.phone || '');
  const [email, setEmail] = useState(lead?.email || '');
  const [copied, setCopied] = useState(false);

  if (!reviewLink) return null;

  const message = buildReviewMessage({ customerName, salesperson, reviewLink });

  const handleSalesperson = (value) => {
    setSalesperson(value);
    try {
      localStorage.setItem(SALESPERSON_STORAGE_KEY, value);
    } catch {
      /* private mode — remembering the name is best-effort */
    }
  };

  const handleCopy = () => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return;
    navigator.clipboard.writeText(message).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };

  const inputClass = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-slate-800 bg-white outline-none focus:border-blue-400';

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 flex flex-col gap-3 max-w-xl" data-testid="review-request-card">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Star size={18} className="text-amber-500" />
          <h3 className="font-semibold text-slate-800 text-sm">Request a Google review</h3>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close review request"
            className="text-gray-400 hover:text-gray-600 text-lg leading-none px-1"
          >
            ✕
          </button>
        )}
      </div>
      <p className="text-xs text-gray-600">
        Prefills a text or email you send from your own phone — nothing is sent automatically.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-gray-700">
          Customer name
          <input
            type="text"
            value={customerName}
            onChange={e => setCustomerName(e.target.value)}
            placeholder="e.g. Maria Lopez"
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-gray-700">
          Your name (remembered)
          <input
            type="text"
            value={salesperson}
            onChange={e => handleSalesperson(e.target.value)}
            placeholder="e.g. Celeste"
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-gray-700">
          Customer phone
          <input
            type="tel"
            value={phone}
            onChange={e => setPhone(e.target.value)}
            placeholder="(281) 555-0123"
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-gray-700">
          Customer email
          <input
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="customer@example.com"
            className={inputClass}
          />
        </label>
      </div>

      <label className="flex flex-col gap-1 text-xs font-medium text-gray-700">
        Message preview
        <textarea
          readOnly
          value={message}
          rows={6}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-slate-700 bg-gray-50 resize-none"
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg border border-gray-300 text-gray-700 bg-white hover:border-gray-400 transition"
        >
          <Copy size={14} />
          {copied ? 'Copied!' : 'Copy message'}
        </button>
        {phone && (
          <a
            href={buildSmsHref(phone, message)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition"
          >
            <MessageSquare size={14} />
            Text customer
          </a>
        )}
        {email && (
          <a
            href={buildMailtoHref(email, EMAIL_SUBJECT, message)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition"
          >
            <Mail size={14} />
            Email customer
          </a>
        )}
      </div>
    </div>
  );
}
