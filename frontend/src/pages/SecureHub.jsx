import React, { useState, useEffect } from 'react';
import {
  Shield,
  FileText,
  Download,
  CheckCircle2,
  Clock,
  Home,
  AlertCircle,
  Loader2,
  Calendar
} from 'lucide-react';
import { BUSINESS_NAME } from '../constants';
import { safeUserMessage, extractErrorMessage, responseErrorMessage } from '../utils/apiError';

export default function SecureHub({ dealId }) {
  const [loading, setLoading] = useState(false);
  const [verified, setVerified] = useState(false);
  const [phone, setPhone] = useState('');
  const [deal, setDeal] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!dealId) {
      setError("No application link found. Please use the secure link we sent you.");
    }
  }, [dealId]);

  // Phone-last-4 verification: a deal link alone must not reveal PII or
  // documents (which carry SSN/DOB). The customer proves ownership with the
  // phone number on their application before anything is fetched.
  const verify = async (e) => {
    if (e) e.preventDefault();
    if (!dealId) return;
    const digits = phone.replace(/\D/g, '');
    if (digits.length < 4) {
      setError("Enter the last 4 digits of the phone number on your application.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(
        `/api/v1/customer/deal/${encodeURIComponent(dealId)}?phone=${encodeURIComponent(digits)}`
      );
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          setDeal(data.deal);
          setDocuments(data.documents);
          setVerified(true);
        } else {
          setError(safeUserMessage(extractErrorMessage(data), "Unable to find your application details."));
        }
      } else if (res.status === 403) {
        setError("That doesn't match the phone number on file. Please try again or call us.");
      } else {
        setError(await responseErrorMessage(res, { context: 'load your application details' }));
      }
    } catch (err) {
      console.error("Secure Hub verify error:", err);
      setError("Connection error. Please try again later.");
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (noteId) => {
    try {
      const digits = phone.replace(/\D/g, '');
      const res = await fetch(
        `/api/v1/customer/deal/${encodeURIComponent(dealId)}/download/${encodeURIComponent(noteId)}?phone=${encodeURIComponent(digits)}`
      );
      const data = await res.json();
      if (data.success && data.url) {
        window.open(data.url, '_blank');
      } else {
        alert("Download failed. Please contact your sales representative.");
      }
    } catch (_err) {
      alert("Error generating download link.");
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <Loader2 className="h-10 w-10 animate-spin text-[var(--cp-accent)]" />
        <p className="text-[var(--cp-muted)] font-medium">Securing your connection...</p>
      </div>
    );
  }

  // Verification gate — shown until the customer proves ownership.
  if (!verified) {
    const validLen = phone.replace(/\D/g, '').length >= 4;
    return (
      <div className="max-w-md mx-auto mt-12 p-8 bg-[var(--cp-panel)] border border-[var(--cp-border)] rounded-2xl">
        <div className="flex items-center justify-center gap-2 text-[var(--cp-accent)] font-bold mb-2">
          <Shield size={20} />
          <span className="tracking-widest uppercase text-xs">Secure Hub</span>
        </div>
        <h2 className="text-xl font-bold text-[var(--cp-text)] mb-2 text-center">Verify it's you</h2>
        <p className="text-[var(--cp-muted)] text-sm mb-6 text-center">
          For your security, enter the last 4 digits of the phone number on your application.
        </p>
        <form onSubmit={verify} className="space-y-4">
          <input
            type="tel"
            inputMode="numeric"
            autoComplete="off"
            aria-label="Last 4 digits of your phone number"
            maxLength={4}
            value={phone}
            onChange={(e) => setPhone(e.target.value.replace(/\D/g, ''))}
            placeholder="••••"
            className="w-full text-center tracking-[0.5em] text-2xl py-3 bg-[var(--cp-surface)] border border-[var(--cp-border)] rounded-lg text-[var(--cp-text)] focus:outline-none focus:ring-2 focus:ring-[var(--cp-accent)]"
          />
          {error && (
            <p className="text-red-500 text-sm flex items-center justify-center gap-2">
              <AlertCircle size={16} /> {error}
            </p>
          )}
          <button
            type="submit"
            disabled={!dealId || !validLen}
            className="w-full px-6 py-3 bg-[var(--cp-accent)] text-white rounded-lg font-semibold hover:opacity-90 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            View My Application
          </button>
        </form>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto mt-12 p-8 bg-[var(--cp-panel)] border border-[var(--cp-border)] rounded-2xl text-center">
        <AlertCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
        <h2 className="text-xl font-bold text-[var(--cp-text)] mb-2">Secure Hub Access Error</h2>
        <p className="text-[var(--cp-muted)] mb-6">{error}</p>
        <button
          onClick={() => window.location.href = '/'}
          className="px-6 py-2 bg-[var(--cp-accent)] text-white rounded-lg font-semibold hover:opacity-90 transition"
        >
          Return to Home
        </button>
      </div>
    );
  }

  const steps = [
    { label: 'Application Submitted', date: deal.created_at, completed: true },
    { label: 'Processing', date: null, completed: deal.status !== 'pending' },
    { label: 'Underwriting', date: null, completed: ['approved', 'contract', 'closed'].includes(deal.status) },
    { label: 'Closing Documents', date: null, completed: ['contract', 'closed'].includes(deal.status) },
    { label: 'Closed', date: null, completed: deal.status === 'closed' },
  ];

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-8 animate-in fade-in duration-500">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 bg-[var(--cp-panel)] p-8 rounded-3xl border border-[var(--cp-border)] shadow-sm">
        <div>
          <div className="flex items-center gap-2 text-[var(--cp-accent)] font-bold mb-2">
            <Shield size={20} />
            <span className="tracking-widest uppercase text-xs">Secure Hub</span>
          </div>
          <h1 className="text-3xl font-black text-[var(--cp-text)] mb-1">
            Welcome back, {deal.buyer_name.split(' ')[0]}
          </h1>
          <p className="text-[var(--cp-muted)] font-medium">
            Track your {deal.home_model || 'home'} application progress.
          </p>
        </div>
        <div className="flex items-center gap-4 bg-[var(--cp-surface)] p-4 rounded-2xl border border-[var(--cp-border)]">
          <div className="bg-[var(--cp-accent-dim)] p-3 rounded-xl">
            <Home className="text-[var(--cp-accent)]" size={24} />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider font-bold text-[var(--cp-muted)]">Current Status</div>
            <div className="text-lg font-bold text-[var(--cp-text)] capitalize">{deal.status}</div>
          </div>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-8">
        {/* Progress Timeline */}
        <div className="md:col-span-2 space-y-6">
          <div className="bg-[var(--cp-panel)] p-8 rounded-3xl border border-[var(--cp-border)]">
            <h2 className="text-xl font-bold text-[var(--cp-text)] mb-8 flex items-center gap-2">
              <Clock size={20} className="text-[var(--cp-accent)]" />
              Application Timeline
            </h2>
            <div className="relative">
              <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-[var(--cp-border)]"></div>
              <div className="space-y-10 relative">
                {steps.map((step, idx) => (
                  <div key={idx} className="flex gap-6 items-start">
                    <div className={`z-10 w-8 h-8 rounded-full flex items-center justify-center border-4 ${
                      step.completed
                        ? 'bg-[var(--cp-accent)] border-[var(--cp-accent-dim)]'
                        : 'bg-[var(--cp-panel)] border-[var(--cp-border)]'
                    }`}>
                      {step.completed && <CheckCircle2 size={16} className="text-white" />}
                    </div>
                    <div>
                      <div className={`font-bold ${step.completed ? 'text-[var(--cp-text)]' : 'text-[var(--cp-muted)]'}`}>
                        {step.label}
                      </div>
                      {step.date && (
                        <div className="text-xs text-[var(--cp-muted)] flex items-center gap-1 mt-1">
                          <Calendar size={12} />
                          {new Date(step.date).toLocaleDateString()}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Documents */}
        <div className="space-y-6">
          <div className="bg-[var(--cp-panel)] p-8 rounded-3xl border border-[var(--cp-border)] h-full">
            <h2 className="text-xl font-bold text-[var(--cp-text)] mb-6 flex items-center gap-2">
              <FileText size={20} className="text-[var(--cp-accent)]" />
              Your Documents
            </h2>

            {documents.length === 0 ? (
              <div className="text-center py-12">
                <FileText className="h-12 w-12 text-[var(--cp-border)] mx-auto mb-3" />
                <p className="text-sm text-[var(--cp-muted)]">No documents available for download yet.</p>
              </div>
            ) : (
              <div className="space-y-3">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="group flex items-center justify-between p-4 rounded-2xl bg-[var(--cp-surface)] border border-[var(--cp-border)] hover:border-[var(--cp-accent)] transition cursor-pointer"
                    onClick={() => handleDownload(doc.id, doc.name)}
                  >
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-[var(--cp-panel)] rounded-lg text-[var(--cp-accent)]">
                        <FileText size={18} />
                      </div>
                      <div>
                        <div className="text-sm font-bold text-[var(--cp-text)] truncate max-w-[140px]">
                          {doc.name.replace('.pdf', '')}
                        </div>
                        <div className="text-[10px] text-[var(--cp-muted)] uppercase tracking-widest font-bold">
                          {doc.status}
                        </div>
                      </div>
                    </div>
                    <Download size={18} className="text-[var(--cp-muted)] group-hover:text-[var(--cp-accent)] transition" />
                  </div>
                ))}
              </div>
            )}

            <div className="mt-8 p-4 bg-[var(--cp-accent-dim)] rounded-2xl border border-[var(--cp-accent-hot)]">
              <p className="text-[11px] text-[var(--cp-accent)] font-semibold leading-relaxed">
                <Shield size={12} className="inline mr-1" />
                All documents are encrypted and stored securely. Only authorized parties can access these files.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
