import React, { useState } from 'react';
import { Bug, Send, X, CheckCircle } from 'lucide-react';

/**
 * Report Issue button — captures page context and sends feedback.
 * Appears as a floating button in the bottom-right corner.
 */
export default function ReportIssue() {
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);

  const submit = async () => {
    if (!description.trim()) return;
    setSending(true);

    const report = {
      description: description.trim(),
      page: window.location.pathname,
      url: window.location.href,
      userAgent: navigator.userAgent,
      screenSize: `${window.innerWidth}x${window.innerHeight}`,
      timestamp: new Date().toISOString(),
    };

    let ok = false;
    try {
      const resp = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(report),
      });
      ok = resp.ok;  // a 4xx/5xx is NOT a successful submission
    } catch {
      ok = false;
    }

    setSending(false);
    if (ok) {
      setSent(true);
      setTimeout(() => { setSent(false); setOpen(false); setDescription(''); }, 2000);
    } else {
      // Don't falsely confirm — let the customer retry or call instead.
      console.warn('Issue report failed to send:', report);
      setFailed(true);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-50 bg-red-500 hover:bg-red-600 text-white p-3 rounded-full shadow-lg transition-all hover:scale-110"
        title="Report an issue"
      >
        <Bug size={20} />
      </button>
    );
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 w-80 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-bold text-gray-800 dark:text-white flex items-center gap-2">
          <Bug size={16} className="text-red-500" /> Report Issue
        </h3>
        <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-600">
          <X size={16} />
        </button>
      </div>

      {sent ? (
        <div className="flex items-center gap-2 text-green-600 py-4">
          <CheckCircle size={20} />
          <span className="font-medium">Thank you! We'll look into it.</span>
        </div>
      ) : failed ? (
        <div className="py-4 text-sm">
          <p role="alert" className="text-red-600 font-medium mb-1">Couldn't send your report.</p>
          <p className="text-gray-500 mb-3">Please try again, or call our showroom and we'll help right away.</p>
          <button
            onClick={() => setFailed(false)}
            className="px-4 py-2 bg-red-500 text-white text-sm font-medium rounded-lg hover:bg-red-600 transition-colors"
          >
            Try again
          </button>
        </div>
      ) : (
        <>
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Describe what happened or what's not working..."
            className="w-full p-3 border border-gray-300 dark:border-gray-600 rounded-lg text-sm resize-none h-24 bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-red-300 outline-none"
            autoFocus
          />
          <div className="flex items-center justify-between mt-3">
            <span className="text-xs text-gray-400">
              Page: {window.location.pathname}
            </span>
            <button
              onClick={submit}
              disabled={!description.trim() || sending}
              className="flex items-center gap-1 px-4 py-2 bg-red-500 text-white text-sm font-medium rounded-lg hover:bg-red-600 disabled:opacity-40 transition-colors"
            >
              <Send size={14} />
              {sending ? 'Sending...' : 'Send Report'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
