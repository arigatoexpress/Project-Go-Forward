import React, { useState, useEffect, useCallback } from 'react';
import {
  Users, Phone, Mail, Calendar, ChevronRight, ChevronDown,
  Search, RefreshCw, Send, X, Clock, Home, DollarSign,
  CheckCircle, AlertCircle, Filter, ArrowLeft,
} from 'lucide-react';
import adminFetch from '../adminFetch';
import './CRM.css';

const STATUS_COLORS = {
  new: '#3b82f6',
  contacted: '#f59e0b',
  qualified: '#8b5cf6',
  converted: '#22c55e',
};

const DEAL_STATUS_COLORS = {
  pending: '#94a3b8',
  approved: '#3b82f6',
  contract: '#f59e0b',
  funded: '#8b5cf6',
  complete: '#22c55e',
  denied: '#ef4444',
  archived: '#6b7280',
};

const DEAL_STATUS_ORDER = ['pending', 'approved', 'contract', 'funded', 'complete'];

function formatDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
  }
}

function formatTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  } catch {
    return '';
  }
}

function timeAgo(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return formatDate(iso);
  } catch {
    return '';
  }
}

// ─── Tabs ────────────────────────────────────────────

const TABS = [
  { id: 'leads', label: 'Leads', icon: Users },
  { id: 'deals', label: 'Pipeline', icon: DollarSign },
  { id: 'appointments', label: 'Appointments', icon: Calendar },
  { id: 'emails', label: 'Email Log', icon: Mail },
];

// ─── Main CRM Component ─────────────────────────────

export default function CRM({ onBack }) {
  const [activeTab, setActiveTab] = useState('leads');
  const [leads, setLeads] = useState([]);
  const [deals, setDeals] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [emails, setEmails] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // Email compose
  const [showEmailCompose, setShowEmailCompose] = useState(false);
  const [emailForm, setEmailForm] = useState({ to: '', customer_name: '', subject: '', message: '' });
  const [emailSending, setEmailSending] = useState(false);
  const [emailResult, setEmailResult] = useState(null);

  // Lead detail
  const [selectedLead, setSelectedLead] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const results = await Promise.allSettled([
        adminFetch('/api/leads?limit=200').then(r => r.json()),
        adminFetch('/api/deals?limit=200').then(r => r.json()),
        adminFetch('/api/crm/appointments?limit=200').then(r => r.json()),
        adminFetch('/api/email/log?limit=100').then(r => r.json()),
      ]);
      const [leadsRes, dealsRes, apptsRes, emailsRes] = results.map(r => r.status === 'fulfilled' ? r.value : {});
      if (leadsRes.success) setLeads(leadsRes.leads || []);
      if (dealsRes.success) setDeals(dealsRes.deals || []);
      if (apptsRes.success) setAppointments(apptsRes.appointments || []);
      if (emailsRes.success) setEmails(emailsRes.emails || []);
    } catch (err) {
      console.error('CRM data fetch failed:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleUpdateLeadStatus = async (leadId, newStatus) => {
    try {
      const res = await adminFetch(`/api/leads/${leadId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      const data = await res.json();
      if (data.success) {
        setLeads(prev => prev.map(l => l.lead_id === leadId ? { ...l, status: newStatus } : l));
        if (selectedLead?.lead_id === leadId) {
          setSelectedLead(prev => ({ ...prev, status: newStatus }));
        }
      }
    } catch (err) {
      console.error('Lead status update failed:', err);
    }
  };

  const handleUpdateDealStatus = async (dealId, newStatus) => {
    try {
      await adminFetch(`/api/deals/${dealId}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      setDeals(prev => prev.map(d => d.id === dealId ? { ...d, status: newStatus } : d));
    } catch (err) {
      console.error('Deal status update failed:', err);
    }
  };

  const handleSendEmail = async (e) => {
    e.preventDefault();
    setEmailSending(true);
    setEmailResult(null);
    try {
      const res = await adminFetch('/api/email/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(emailForm),
      });
      const data = await res.json();
      setEmailResult(data);
      if (data.success) {
        setEmailForm({ to: '', customer_name: '', subject: '', message: '' });
        // Refresh email log
        const emailsRes = await adminFetch('/api/email/log?limit=100').then(r => r.json());
        if (emailsRes.success) setEmails(emailsRes.emails || []);
      }
    } catch (err) {
      setEmailResult({ success: false, error: err.message });
    } finally {
      setEmailSending(false);
    }
  };

  const openEmailForLead = (lead) => {
    setEmailForm({
      to: lead.email || '',
      customer_name: lead.name || '',
      subject: '',
      message: '',
    });
    setShowEmailCompose(true);
  };

  // ─── Filtering ──────────────

  const filteredLeads = leads.filter(l => {
    const q = searchQuery.toLowerCase();
    const matchesQ = !q || (l.name || '').toLowerCase().includes(q) || (l.phone || '').includes(q) || (l.email || '').toLowerCase().includes(q);
    const matchesStatus = !statusFilter || l.status === statusFilter;
    return matchesQ && matchesStatus;
  });

  const filteredDeals = deals.filter(d => {
    const q = searchQuery.toLowerCase();
    const name = `${d.buyer_first_name || ''} ${d.buyer_last_name || ''}`.toLowerCase();
    const matchesQ = !q || name.includes(q) || (d.model || '').toLowerCase().includes(q) || (d.id || '').toLowerCase().includes(q);
    const matchesStatus = !statusFilter || d.status === statusFilter;
    return matchesQ && matchesStatus;
  });

  const filteredAppointments = appointments.filter(a => {
    const q = searchQuery.toLowerCase();
    const matchesQ = !q || (a.name || '').toLowerCase().includes(q) || (a.phone || '').includes(q);
    const matchesStatus = !statusFilter || a.status === statusFilter;
    return matchesQ && matchesStatus;
  });

  // ─── Stats ──────────────

  const stats = {
    totalLeads: leads.length,
    newLeads: leads.filter(l => l.status === 'new').length,
    activeDeals: deals.filter(d => !['complete', 'denied', 'archived'].includes(d.status)).length,
    upcomingAppts: appointments.filter(a => a.status === 'confirmed').length,
    emailsSent: emails.length,
  };

  return (
    <div className="crm-container">
      {/* Header */}
      <header className="crm-header">
        <div className="crm-header-left">
          <button onClick={onBack} className="crm-back-btn" aria-label="Go back">
            <ArrowLeft size={20} />
          </button>
          <h1 className="crm-title">CRM Dashboard</h1>
        </div>
        <button onClick={fetchData} className="crm-refresh-btn" disabled={loading}>
          <RefreshCw size={16} className={loading ? 'crm-spin' : ''} />
          Refresh
        </button>
      </header>

      {/* Stats Bar */}
      <div className="crm-stats-bar">
        <div className="crm-stat">
          <span className="crm-stat-value">{stats.totalLeads}</span>
          <span className="crm-stat-label">Total Leads</span>
        </div>
        <div className="crm-stat">
          <span className="crm-stat-value" style={{ color: '#3b82f6' }}>{stats.newLeads}</span>
          <span className="crm-stat-label">New</span>
        </div>
        <div className="crm-stat">
          <span className="crm-stat-value" style={{ color: '#8b5cf6' }}>{stats.activeDeals}</span>
          <span className="crm-stat-label">Active Deals</span>
        </div>
        <div className="crm-stat">
          <span className="crm-stat-value" style={{ color: '#22c55e' }}>{stats.upcomingAppts}</span>
          <span className="crm-stat-label">Appointments</span>
        </div>
        <div className="crm-stat">
          <span className="crm-stat-value" style={{ color: '#f59e0b' }}>{stats.emailsSent}</span>
          <span className="crm-stat-label">Emails Sent</span>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="crm-tabs">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`crm-tab ${activeTab === tab.id ? 'crm-tab-active' : ''}`}
            onClick={() => { setActiveTab(tab.id); setStatusFilter(''); setSearchQuery(''); }}
          >
            <tab.icon size={16} />
            {tab.label}
          </button>
        ))}
        <button className="crm-tab crm-compose-btn" onClick={() => setShowEmailCompose(true)}>
          <Send size={16} />
          Compose
        </button>
      </div>

      {/* Search & Filter */}
      <div className="crm-toolbar">
        <div className="crm-search-wrap">
          <Search size={16} />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search by name, phone, email..."
            className="crm-search-input"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="crm-search-clear">
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="crm-content">
        {loading && <div className="crm-loading">Loading...</div>}

        {/* LEADS TAB */}
        {activeTab === 'leads' && !loading && (
          <>
            <div className="crm-status-filters">
              {['', 'new', 'contacted', 'qualified', 'converted'].map(s => (
                <button
                  key={s}
                  className={`crm-status-chip ${statusFilter === s ? 'crm-status-chip-active' : ''}`}
                  onClick={() => setStatusFilter(s)}
                  style={s && statusFilter === s ? { borderColor: STATUS_COLORS[s], color: STATUS_COLORS[s] } : {}}
                >
                  {s || 'All'} {s ? `(${leads.filter(l => l.status === s).length})` : `(${leads.length})`}
                </button>
              ))}
            </div>

            {selectedLead ? (
              <LeadDetail
                lead={selectedLead}
                onClose={() => setSelectedLead(null)}
                onUpdateStatus={handleUpdateLeadStatus}
                onEmail={openEmailForLead}
                appointments={appointments.filter(a =>
                  a.phone && selectedLead.phone && a.phone.replace(/\D/g, '') === selectedLead.phone.replace(/\D/g, '')
                )}
                emails={emails.filter(e => e.to === selectedLead.email)}
              />
            ) : (
              <div className="crm-list">
                {filteredLeads.length === 0 && <div className="crm-empty">No leads found</div>}
                {filteredLeads.map(lead => (
                  <div key={lead.lead_id} className="crm-lead-card" onClick={() => setSelectedLead(lead)}>
                    <div className="crm-lead-avatar" style={{ background: STATUS_COLORS[lead.status] || '#94a3b8' }}>
                      {(lead.name || '?')[0].toUpperCase()}
                    </div>
                    <div className="crm-lead-info">
                      <div className="crm-lead-name">{lead.name || 'Unknown'}</div>
                      <div className="crm-lead-meta">
                        {lead.phone && <span><Phone size={12} /> {lead.phone}</span>}
                        {lead.email && <span><Mail size={12} /> {lead.email}</span>}
                        {lead.source && <span className="crm-lead-source">{lead.source}</span>}
                      </div>
                    </div>
                    <div className="crm-lead-right">
                      <span className="crm-lead-status" style={{ background: STATUS_COLORS[lead.status] || '#94a3b8' }}>
                        {lead.status}
                      </span>
                      <span className="crm-lead-time">{timeAgo(lead.created_at)}</span>
                    </div>
                    <ChevronRight size={16} className="crm-lead-chevron" />
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* PIPELINE TAB */}
        {activeTab === 'deals' && !loading && (
          <div className="crm-pipeline">
            {DEAL_STATUS_ORDER.map(status => {
              const statusDeals = filteredDeals.filter(d => d.status === status);
              return (
                <div key={status} className="crm-pipeline-col">
                  <div className="crm-pipeline-header" style={{ borderTopColor: DEAL_STATUS_COLORS[status] }}>
                    <span className="crm-pipeline-status">{status.charAt(0).toUpperCase() + status.slice(1)}</span>
                    <span className="crm-pipeline-count">{statusDeals.length}</span>
                  </div>
                  <div className="crm-pipeline-cards">
                    {statusDeals.map(deal => (
                      <DealCard
                        key={deal.id}
                        deal={deal}
                        onUpdateStatus={handleUpdateDealStatus}
                        statusOrder={DEAL_STATUS_ORDER}
                      />
                    ))}
                    {statusDeals.length === 0 && (
                      <div className="crm-pipeline-empty">No deals</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* APPOINTMENTS TAB */}
        {activeTab === 'appointments' && !loading && (
          <>
            <div className="crm-status-filters">
              {['', 'confirmed', 'completed', 'cancelled', 'no_show'].map(s => (
                <button
                  key={s}
                  className={`crm-status-chip ${statusFilter === s ? 'crm-status-chip-active' : ''}`}
                  onClick={() => setStatusFilter(s)}
                >
                  {s || 'All'} ({s ? appointments.filter(a => a.status === s).length : appointments.length})
                </button>
              ))}
            </div>
            <div className="crm-list">
              {filteredAppointments.length === 0 && <div className="crm-empty">No appointments found</div>}
              {filteredAppointments.map(appt => (
                <div key={appt.appointment_id} className="crm-appt-card">
                  <div className="crm-appt-date">
                    <Calendar size={16} />
                    <strong>{appt.date}</strong>
                    <span>{appt.time_slot}</span>
                  </div>
                  <div className="crm-appt-info">
                    <div className="crm-appt-name">{appt.name}</div>
                    <div className="crm-appt-meta">
                      <span><Phone size={12} /> {appt.phone}</span>
                      {appt.email && <span><Mail size={12} /> {appt.email}</span>}
                      {appt.notes && <span className="crm-appt-notes">{appt.notes}</span>}
                    </div>
                  </div>
                  <span className={`crm-appt-status crm-appt-status-${appt.status}`}>
                    {appt.status}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {/* EMAIL LOG TAB */}
        {activeTab === 'emails' && !loading && (
          <div className="crm-list">
            {emails.length === 0 && <div className="crm-empty">No emails sent yet</div>}
            {emails.map((email, idx) => (
              <div key={idx} className="crm-email-card">
                <div className="crm-email-icon">
                  <Mail size={16} />
                </div>
                <div className="crm-email-info">
                  <div className="crm-email-subject">{email.subject}</div>
                  <div className="crm-email-meta">
                    <span>To: {email.to}</span>
                    <span className="crm-email-type">{email.email_type}</span>
                  </div>
                </div>
                <div className="crm-email-time">{timeAgo(email.sent_at)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Email Compose Modal */}
      {showEmailCompose && (
        <div className="crm-modal-overlay" onClick={() => { setShowEmailCompose(false); setEmailResult(null); }}>
          <div className="crm-email-modal" onClick={e => e.stopPropagation()}>
            <div className="crm-email-modal-header">
              <h3>Compose Email</h3>
              <button onClick={() => { setShowEmailCompose(false); setEmailResult(null); }} className="crm-modal-close">
                <X size={20} />
              </button>
            </div>
            <form onSubmit={handleSendEmail} className="crm-email-form">
              <div className="crm-email-field">
                <label>To</label>
                <input
                  type="email"
                  value={emailForm.to}
                  onChange={e => setEmailForm(f => ({ ...f, to: e.target.value }))}
                  placeholder="customer@email.com"
                  required
                />
              </div>
              <div className="crm-email-field">
                <label>Name</label>
                <input
                  type="text"
                  value={emailForm.customer_name}
                  onChange={e => setEmailForm(f => ({ ...f, customer_name: e.target.value }))}
                  placeholder="Customer name"
                />
              </div>
              <div className="crm-email-field">
                <label>Subject</label>
                <input
                  type="text"
                  value={emailForm.subject}
                  onChange={e => setEmailForm(f => ({ ...f, subject: e.target.value }))}
                  placeholder="Email subject"
                  required
                />
              </div>
              <div className="crm-email-field">
                <label>Message</label>
                <textarea
                  value={emailForm.message}
                  onChange={e => setEmailForm(f => ({ ...f, message: e.target.value }))}
                  placeholder="Write your message..."
                  rows={6}
                  required
                />
              </div>
              {emailResult && (
                <div className={`crm-email-result ${emailResult.success ? 'crm-email-result-ok' : 'crm-email-result-err'}`}>
                  {emailResult.success ? (
                    <><CheckCircle size={16} /> Email sent successfully</>
                  ) : (
                    <><AlertCircle size={16} /> {emailResult.error || 'Send failed'}</>
                  )}
                </div>
              )}
              <button type="submit" className="crm-email-send-btn" disabled={emailSending}>
                <Send size={16} />
                {emailSending ? 'Sending...' : 'Send Email'}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Lead Detail Panel ──────────────────────────────

function LeadDetail({ lead, onClose, onUpdateStatus, onEmail, appointments, emails }) {
  return (
    <div className="crm-lead-detail">
      <button onClick={onClose} className="crm-detail-back">
        <ArrowLeft size={16} /> Back to leads
      </button>

      <div className="crm-detail-header">
        <div className="crm-detail-avatar" style={{ background: STATUS_COLORS[lead.status] || '#94a3b8' }}>
          {(lead.name || '?')[0].toUpperCase()}
        </div>
        <div>
          <h2 className="crm-detail-name">{lead.name || 'Unknown'}</h2>
          <div className="crm-detail-contact">
            {lead.phone && <span><Phone size={14} /> {lead.phone}</span>}
            {lead.email && <span><Mail size={14} /> {lead.email}</span>}
          </div>
        </div>
      </div>

      {/* Status Actions */}
      <div className="crm-detail-section">
        <h3>Status</h3>
        <div className="crm-detail-status-row">
          {['new', 'contacted', 'qualified', 'converted'].map(s => (
            <button
              key={s}
              className={`crm-detail-status-btn ${lead.status === s ? 'crm-detail-status-active' : ''}`}
              style={lead.status === s ? { background: STATUS_COLORS[s], color: '#fff' } : {}}
              onClick={() => onUpdateStatus(lead.lead_id, s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Preferences */}
      {(lead.bedrooms || lead.bathrooms || lead.budget_max || lead.home_type) && (
        <div className="crm-detail-section">
          <h3>Preferences</h3>
          <div className="crm-detail-prefs">
            {lead.bedrooms && <span><Home size={14} /> {lead.bedrooms} bed</span>}
            {lead.bathrooms && <span>{lead.bathrooms} bath</span>}
            {lead.budget_max && <span><DollarSign size={14} /> {Number(lead.budget_max).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}</span>}
            {lead.home_type && <span>{lead.home_type}</span>}
          </div>
        </div>
      )}

      {/* Engagement */}
      <div className="crm-detail-section">
        <h3>Engagement</h3>
        <div className="crm-detail-engagement">
          <span>Source: <strong>{lead.source}</strong></span>
          <span>Appointment: <strong>{lead.appointment_requested ? 'Yes' : 'No'}</strong></span>
          <span>Financing: <strong>{lead.financing_discussed ? 'Discussed' : 'No'}</strong></span>
          {lead.homes_viewed?.length > 0 && (
            <span>Viewed: <strong>{lead.homes_viewed.join(', ')}</strong></span>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="crm-detail-section">
        <h3>Actions</h3>
        <div className="crm-detail-actions">
          {lead.phone && (
            <a href={`tel:${lead.phone}`} className="crm-action-btn crm-action-call">
              <Phone size={16} /> Call
            </a>
          )}
          {lead.email && (
            <button onClick={() => onEmail(lead)} className="crm-action-btn crm-action-email">
              <Mail size={16} /> Email
            </button>
          )}
        </div>
      </div>

      {/* Activity Timeline */}
      <div className="crm-detail-section">
        <h3>Activity</h3>
        <div className="crm-timeline">
          {/* Created */}
          <div className="crm-timeline-item">
            <div className="crm-timeline-dot" style={{ background: '#3b82f6' }} />
            <div className="crm-timeline-content">
              <span className="crm-timeline-action">Lead created via {lead.source}</span>
              <span className="crm-timeline-time">{formatDate(lead.created_at)}</span>
            </div>
          </div>

          {/* Related Appointments */}
          {appointments.map(appt => (
            <div key={appt.appointment_id} className="crm-timeline-item">
              <div className="crm-timeline-dot" style={{ background: '#22c55e' }} />
              <div className="crm-timeline-content">
                <span className="crm-timeline-action">
                  Appointment {appt.status} — {appt.date} at {appt.time_slot}
                </span>
                <span className="crm-timeline-time">{formatDate(appt.created_at)}</span>
              </div>
            </div>
          ))}

          {/* Related Emails */}
          {emails.map((email, idx) => (
            <div key={idx} className="crm-timeline-item">
              <div className="crm-timeline-dot" style={{ background: '#f59e0b' }} />
              <div className="crm-timeline-content">
                <span className="crm-timeline-action">
                  Email sent: {email.subject}
                </span>
                <span className="crm-timeline-time">{formatDate(email.sent_at)}</span>
              </div>
            </div>
          ))}

          {appointments.length === 0 && emails.length === 0 && (
            <div className="crm-timeline-empty">No activity yet beyond lead creation</div>
          )}
        </div>
      </div>

      <div className="crm-detail-meta">
        <span>ID: {lead.lead_id}</span>
        <span>Created: {formatDate(lead.created_at)}</span>
        <span>Updated: {formatDate(lead.updated_at)}</span>
      </div>
    </div>
  );
}

// ─── Deal Card (Pipeline) ───────────────────────────

function DealCard({ deal, onUpdateStatus, statusOrder }) {
  const [expanded, setExpanded] = useState(false);
  const currentIdx = statusOrder.indexOf(deal.status);
  const nextStatus = currentIdx >= 0 && currentIdx < statusOrder.length - 1 ? statusOrder[currentIdx + 1] : null;
  const buyerName = `${deal.buyer_first_name || ''} ${deal.buyer_last_name || ''}`.trim();

  return (
    <div className="crm-deal-card">
      <div className="crm-deal-header" onClick={() => setExpanded(!expanded)}>
        <div>
          <div className="crm-deal-name">{buyerName || 'No Name'}</div>
          <div className="crm-deal-model">{deal.model || 'No home selected'}</div>
        </div>
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </div>

      {deal.sales_price && (
        <div className="crm-deal-price">
          ${Number(deal.sales_price).toLocaleString()}
        </div>
      )}

      {expanded && (
        <div className="crm-deal-details">
          {deal.buyer_phone && <div><Phone size={12} /> {deal.buyer_phone}</div>}
          {deal.buyer_email && <div><Mail size={12} /> {deal.buyer_email}</div>}
          {deal.salesrep && <div>Rep: {deal.salesrep}</div>}
          {deal.manufacturer && <div>{deal.manufacturer}</div>}
          <div className="crm-deal-dates">
            Created: {formatDate(deal.created_at)}
          </div>
        </div>
      )}

      {nextStatus && (
        <button
          className="crm-deal-advance"
          onClick={() => onUpdateStatus(deal.id, nextStatus)}
        >
          Move to {nextStatus} →
        </button>
      )}
    </div>
  );
}
