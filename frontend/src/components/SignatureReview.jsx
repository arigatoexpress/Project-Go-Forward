import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, Download, Loader, Send, X } from 'lucide-react';
import adminFetch from '../adminFetch';
import downloadAdminFile from '../downloadAdminFile';
import { safeUserMessage } from '../utils/apiError';

const TEMPLATE_LABELS = {
  'TMHA_SalesContract.pdf': 'Sales Contract',
  'TMHA-SalesContractDepositAgreement.pdf': 'Deposit Agreement',
};

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    return {};
  }
}

function formatSentAt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Send-for-signature with a mandatory staff review step.
 *
 * 1. "Review & Send for Signature" builds the document from the deal and shows
 *    the payment lines plus any reasons it can't be sent. Nothing is sent.
 * 2. Staff open the filled PDF, tick the hand-check box, and confirm.
 * 3. The server rebuilds the document and refuses if anything changed since
 *    the preview (review_token mismatch) or a money line is blank.
 */
export default function SignatureReview({ deal, templateName = 'TMHA_SalesContract.pdf', disabled = false }) {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [checked, setChecked] = useState(false);
  const [message, setMessage] = useState(null);
  const [requests, setRequests] = useState([]);
  const [cancelling, setCancelling] = useState(null);

  const label = TEMPLATE_LABELS[templateName] || templateName;

  const loadRequests = useCallback(async () => {
    try {
      const res = await adminFetch(`/api/deals/${deal.id}/esign`);
      const data = await readJson(res);
      if (res.ok && Array.isArray(data.requests)) setRequests(data.requests);
    } catch {
      // Listing is informational; the send flow does not depend on it.
    }
  }, [deal.id]);

  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  const handleReview = async () => {
    setLoading(true);
    setMessage(null);
    setChecked(false);
    try {
      const res = await adminFetch(`/api/deals/${deal.id}/esign/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: templateName }),
      });
      const data = await readJson(res);
      if (!res.ok) {
        setMessage({ ok: false, text: safeUserMessage(data.message, 'Could not build the preview. Please try again.') });
      } else {
        setPreview(data);
      }
    } catch {
      setMessage({ ok: false, text: 'Could not build the preview. Please try again.' });
    }
    setLoading(false);
  };

  const handleSend = async () => {
    if (!preview?.ready || !checked) return;
    setSending(true);
    setMessage(null);
    try {
      const res = await adminFetch(`/api/deals/${deal.id}/esign/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_name: templateName,
          confirm: true,
          review_token: preview.review_token,
        }),
      });
      const data = await readJson(res);
      if (res.ok && data.success) {
        setMessage({ ok: true, text: data.message || `${label} sent for signature.` });
        setPreview(null);
        loadRequests();
      } else {
        setMessage({ ok: false, text: safeUserMessage(data.message, 'Not sent. Please try again.') });
        if (Array.isArray(data.problems) && data.money_summary) {
          setPreview({ ...data, esign_configured: preview.esign_configured, ready: Boolean(data.ready) });
          setChecked(false);
        }
      }
    } catch {
      setMessage({ ok: false, text: 'Not sent. Please try again.' });
    }
    setSending(false);
  };

  const handleCancelRequest = async (submissionId) => {
    if (!window.confirm('Cancel this signing request? The buyer will no longer be able to sign it.')) return;
    setCancelling(submissionId);
    setMessage(null);
    try {
      const res = await adminFetch(`/api/deals/${deal.id}/esign/${submissionId}/cancel`, { method: 'POST' });
      const data = await readJson(res);
      setMessage({
        ok: res.ok && data.success,
        text: safeUserMessage(data.message, res.ok ? 'Signing request cancelled.' : 'Could not cancel. Please try again.'),
      });
      loadRequests();
    } catch {
      setMessage({ ok: false, text: 'Could not cancel. Please try again.' });
    }
    setCancelling(null);
  };

  const pending = requests.filter((r) => r.status === 'pending');
  const problems = preview?.problems || [];
  const canSend = Boolean(preview?.ready && preview?.esign_configured && checked && !sending);

  return (
    <div className="mt-2 flex flex-col gap-1.5" data-testid="signature-review">
      <div>
        <button
          type="button"
          onClick={handleReview}
          disabled={disabled || loading || sending}
          className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-md border border-indigo-500 text-indigo-600 bg-white cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? <Loader size={12} className="animate-spin" /> : <Send size={12} />}
          Review &amp; Send for Signature
        </button>
      </div>

      {preview && (
        <div className="p-2 rounded-md border border-indigo-300 bg-indigo-50 text-xs text-slate-800" role="region" aria-label="Signature review">
          <div className="flex items-center justify-between font-semibold">
            <span>Review {preview.document_label || label} before sending</span>
            <button type="button" aria-label="Close review" onClick={() => setPreview(null)} className="bg-transparent border-0 cursor-pointer text-gray-600">
              <X size={14} />
            </button>
          </div>

          {problems.length > 0 && (
            <div className="mt-1.5 p-2 rounded border border-red-400 bg-red-50 text-red-800">
              <div className="flex items-center gap-1 font-semibold">
                <AlertCircle size={13} /> Can&apos;t send yet. Fix these on the deal first:
              </div>
              <ul className="mt-1 ml-4 list-disc">
                {problems.map((p, i) => (
                  <li key={`${p.field || p.code}-${i}`}>{p.message}</li>
                ))}
              </ul>
            </div>
          )}

          <table className="mt-1.5 w-full">
            <tbody>
              {(preview.money_summary || []).map((row) => (
                <tr key={row.label}>
                  <td className="pr-2 text-gray-700">{row.label}</td>
                  <td className="text-right font-semibold">{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {preview.download_url && (
            <button
              type="button"
              onClick={() => downloadAdminFile(preview.download_url, preview.filename).catch(() => {})}
              className="mt-1.5 flex items-center gap-1 px-2 py-1 text-[11px] rounded border border-gray-400 bg-white cursor-pointer"
            >
              <Download size={12} /> Open the filled {preview.document_label || label}
            </button>
          )}

          {preview.ready && (
            <>
              {!preview.esign_configured && (
                <div className="mt-1.5 text-indigo-700">
                  E-signing isn&apos;t turned on yet, so nothing can be sent.
                </div>
              )}
              <label className="mt-1.5 flex items-start gap-1.5">
                <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                <span>I hand-checked the payment lines above and on the filled document.</span>
              </label>
              <button
                type="button"
                onClick={handleSend}
                disabled={!canSend}
                className="mt-1.5 flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-md border-0 bg-indigo-600 text-white cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {sending ? <Loader size={12} className="animate-spin" /> : <Send size={12} />}
                Send to {preview.signer_email}
              </button>
            </>
          )}
        </div>
      )}

      {message && (
        <div
          role="status"
          className={
            'p-2 rounded-md text-xs border flex items-center gap-1.5 ' +
            (message.ok ? 'bg-green-500/10 border-green-500 text-green-800' : 'bg-red-500/10 border-red-500 text-red-800')
          }
        >
          {message.ok ? <CheckCircle size={13} /> : <AlertCircle size={13} />}
          <span>{message.text}</span>
        </div>
      )}

      {pending.length > 0 && (
        <div className="text-xs text-gray-700">
          <div className="font-semibold uppercase tracking-wide text-[11px]">Waiting for signature</div>
          {pending.map((r) => (
            <div key={r.submission_id} className="mt-1 flex items-center justify-between gap-2">
              <span>
                {TEMPLATE_LABELS[r.template_name] || r.template_name}
                {formatSentAt(r.created_at) ? ` · sent ${formatSentAt(r.created_at)}` : ''}
              </span>
              <button
                type="button"
                onClick={() => handleCancelRequest(r.submission_id)}
                disabled={cancelling === r.submission_id}
                className="px-2 py-0.5 text-[11px] rounded border border-red-400 text-red-700 bg-white cursor-pointer disabled:opacity-50"
              >
                {cancelling === r.submission_id ? 'Cancelling…' : 'Cancel request'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
