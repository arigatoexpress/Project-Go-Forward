import React, { useState, useEffect, useCallback } from 'react';
import {
  Users, Phone, Mail, Calendar, ChevronRight, ChevronDown,
  Search, RefreshCw, Send, X, Clock, Home, DollarSign,
  CheckCircle, AlertCircle, Filter, ArrowLeft, FileText, Package, Download, Loader, Copy, Star,
} from 'lucide-react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RechartsTooltip } from 'recharts';
import adminFetch from '../adminFetch';
import downloadAdminFile from '../downloadAdminFile';
import StatusBadge, { STATUS_COLORS, DEAL_STATUS_COLORS } from '../components/StatusBadge';
import ReviewRequestCard from '../components/ReviewRequestCard';

const DEAL_STATUS_ORDER = ['pending', 'approved', 'contract', 'funded', 'complete'];

function formatDate(iso) {
  // Returns empty string for missing values — callers should hide the
  // surrounding row/label rather than render a placeholder.
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
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

// ─── Copy-to-clipboard button ───
// Copies `value` and flashes a brief "Copied" state. stopPropagation keeps the
// click from triggering an enclosing row's open-detail handler.
function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e) => {
    e.stopPropagation();
    e.preventDefault();
    if (typeof navigator === 'undefined' || !navigator.clipboard) return;
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };
  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={copied ? 'Copied' : label}
      className="inline-flex items-center text-gray-400 hover:text-gray-600 transition"
    >
      {copied ? <span className="text-[10px] font-semibold text-emerald-600">Copied</span> : <Copy size={12} />}
    </button>
  );
}

// ─── Tabs ────────────────────────────────────────────

const TABS = [
  { id: 'leads', label: 'Leads', icon: Users },
  { id: 'deals', label: 'Pipeline', icon: DollarSign },
  { id: 'tasks', label: 'Tasks', icon: CheckCircle },
  { id: 'appointments', label: 'Appointments', icon: Calendar },
  { id: 'emails', label: 'Email Log', icon: Mail },
  { id: 'customers', label: 'Customers', icon: Users },
  { id: 'reviews', label: 'Reviews', icon: Star, requiresReviewLink: true },
];

// Email Templates
const EMAIL_TEMPLATES = [
  {
    id: 'welcome',
    name: 'Welcome Email',
    subject: 'Welcome to Texas Home Outlet!',
    body: `Hi {{name}},

Thank you for your interest in Texas Home Outlet! We're excited to help you find your perfect home.

A few things we can help with:
• Browse our current inventory
• Schedule a showroom visit
• Answer questions about financing
• Compare different home models

Feel free to reply to this email or call us at (210) 555-0123.

Best regards,
Texas Home Outlet Team`
  },
  {
    id: 'followup',
    name: 'Follow-up',
    subject: 'Following up on your home search',
    body: `Hi {{name}},

I wanted to follow up on your recent inquiry about manufactured homes. 

Have you had a chance to think about:
• Your preferred number of bedrooms/bathrooms?
• Your budget range?
• When you'd like to schedule a visit?

We're here to help make the process easy. Let me know if you have any questions!

Best,
Texas Home Outlet`
  },
  {
    id: 'appointment_confirmation',
    name: 'Appointment Confirmed',
    subject: 'Your appointment is confirmed - {{date}}',
    body: `Hi {{name}},

Your appointment is confirmed!

📅 Date: {{date}}
🕐 Time: {{time}}
📍 Location: 123 Main Street, San Antonio, TX 78201

What to bring:
• Valid ID
• Proof of income (if interested in financing)
• Any questions you have!

If you need to reschedule, please call us at (210) 555-0123.

Looking forward to meeting you!

Texas Home Outlet Team`
  },
  {
    id: 'price_quote',
    name: 'Price Quote',
    subject: 'Your price quote for {{model}}',
    body: `Hi {{name}},

Thank you for your interest in the {{model}}. Here's your personalized quote:

Home: {{model}}
Manufacturer: {{manufacturer}}
Base Price: {{price}}

Additional options and delivery costs can be discussed during your showroom visit.

Would you like to schedule a time to see this home in person?

Best regards,
Texas Home Outlet Team`
  },
];

// ─── Main CRM Component ─────────────────────────────

export default function CRM({ onBack }) {
  const [activeTab, setActiveTab] = useState('leads');
  const [actionError, setActionError] = useState('');
  const [leads, setLeads] = useState([]);
  const [deals, setDeals] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [emails, setEmails] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // Email compose
  const [showEmailCompose, setShowEmailCompose] = useState(false);
  const [emailForm, setEmailForm] = useState({ to: '', customer_name: '', subject: '', message: '' });
  const [emailSending, setEmailSending] = useState(false);
  const [emailResult, setEmailResult] = useState(null);
  const [selectedTemplate, setSelectedTemplate] = useState('');

  // Lead detail
  const [selectedLead, setSelectedLead] = useState(null);

  // Review-request helper (hidden until GOOGLE_REVIEW_LINK is configured)
  const [reviewLink, setReviewLink] = useState('');
  const [reviewLead, setReviewLead] = useState(null);
  
  // Task management
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [taskForm, setTaskForm] = useState({
    title: '',
    description: '',
    due_date: '',
    priority: 'medium',
    assigned_to: '',
    related_lead: '',
    related_deal: ''
  });

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const results = await Promise.allSettled([
        adminFetch('/api/leads?limit=200').then(r => r.json()),
        adminFetch('/api/deals?limit=200').then(r => r.json()),
        adminFetch('/api/crm/appointments?limit=200').then(r => r.json()),
        adminFetch('/api/email/log?limit=100').then(r => r.json()),
        adminFetch('/api/crm/tasks?limit=200').then(r => r.json()),
      ]);
      const [leadsRes, dealsRes, apptsRes, emailsRes, tasksRes] = results.map(r => r.status === 'fulfilled' ? r.value : {});
      if (leadsRes.success) setLeads(leadsRes.leads || []);
      if (dealsRes.success) setDeals(dealsRes.deals || []);
      if (apptsRes.success) setAppointments(apptsRes.appointments || []);
      if (emailsRes.success) setEmails(emailsRes.emails || []);
      if (tasksRes.success) setTasks(tasksRes.tasks || []);
    } catch (err) {
      console.error('CRM data fetch failed:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // One-time reviews config fetch: no link configured → feature stays hidden.
  useEffect(() => {
    adminFetch('/api/admin/reviews/config')
      .then(r => r.json())
      .then(data => { if (data.success && data.enabled) setReviewLink(data.review_link); })
      .catch(() => {});
  }, []);

  const handleUpdateLeadStatus = async (leadId, newStatus) => {
    setActionError('');
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
      } else {
        setActionError(data.error || `Failed to update lead status — please try again.`);
      }
    } catch (err) {
      console.error('Lead status update failed:', err);
      setActionError('Lead status update failed. Check connection and try again.');
    }
  };

  const handleUpdateDealStatus = async (dealId, newStatus) => {
    setActionError('');
    try {
      const res = await adminFetch(`/api/deals/${dealId}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || data.error || `${res.status} ${res.statusText}`);
      }
      setDeals(prev => prev.map(d => d.id === dealId ? { ...d, status: newStatus } : d));
    } catch (err) {
      console.error('Deal status update failed:', err);
      setActionError(`Deal status update failed: ${err.message || 'unknown error'}. Try again or refresh.`);
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
    setSelectedTemplate('');
    setShowEmailCompose(true);
  };
  
  const applyEmailTemplate = (templateId) => {
    const template = EMAIL_TEMPLATES.find(t => t.id === templateId);
    if (!template) return;
    
    let body = template.body;
    let subject = template.subject;
    
    // Replace variables
    body = body.replace(/{{name}}/g, emailForm.customer_name || 'Customer');
    subject = subject.replace(/{{name}}/g, emailForm.customer_name || 'Customer');
    
    setEmailForm(prev => ({
      ...prev,
      subject,
      message: body
    }));
    setSelectedTemplate(templateId);
  };
  
  // Task management functions
  const handleCreateTask = async (e) => {
    e.preventDefault();
    try {
      const res = await adminFetch('/api/crm/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(taskForm),
      });
      const data = await res.json();
      if (data.success) {
        setTasks(prev => [data.task, ...prev]);
        setShowTaskModal(false);
        setTaskForm({
          title: '',
          description: '',
          due_date: '',
          priority: 'medium',
          assigned_to: '',
          related_lead: '',
          related_deal: ''
        });
      }
    } catch (err) {
      console.error('Task creation failed:', err);
    }
  };
  
  const handleUpdateTaskStatus = async (taskId, status) => {
    try {
      const res = await adminFetch(`/api/crm/tasks/${taskId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      const data = await res.json();
      if (data.success) {
        setTasks(prev => prev.map(t => t.task_id === taskId ? { ...t, status } : t));
      }
    } catch (err) {
      console.error('Task update failed:', err);
    }
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
    const homeText = `${d.home_label || ''} ${d.model || ''} ${d.home_model || ''} ${d.serial_number_1 || ''} ${d.serial_number || ''}`.toLowerCase();
    const matchesQ = !q || name.includes(q) || homeText.includes(q) || (d.id || '').toLowerCase().includes(q);
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
    pendingTasks: tasks.filter(t => t.status === 'pending').length,
  };

  // Stat tile colors — inline so Tailwind picks them up at build time.
  const STAT_TILES = [
    { label: 'Total Leads', value: stats.totalLeads, valueClass: 'text-slate-800' },
    { label: 'New', value: stats.newLeads, valueClass: 'text-blue-700' },
    { label: 'Active Deals', value: stats.activeDeals, valueClass: 'text-violet-700' },
    { label: 'Appointments', value: stats.upcomingAppts, valueClass: 'text-green-700' },
    { label: 'Emails Sent', value: stats.emailsSent, valueClass: 'text-amber-700' },
    { label: 'Pending Tasks', value: stats.pendingTasks, valueClass: 'text-pink-700' },
  ];

  return (
    <div className="min-h-screen bg-gray-50 font-sans">
      {/* Header */}
      <header className="bg-slate-800 text-white px-6 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="bg-white/10 hover:bg-white/20 text-white p-2 rounded-lg flex items-center"
            aria-label="Go back"
          >
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-xl font-bold m-0">CRM Dashboard</h1>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          className="flex items-center gap-1.5 bg-white/10 hover:bg-white/20 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3.5 py-2 rounded-lg text-[13px]"
        >
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </header>

      {actionError && (
        <div
          role="alert"
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 50,
            background: '#fef2f2',
            border: '2px solid #fecaca',
            color: '#991b1b',
            padding: '0.75rem 1rem',
            margin: '0.5rem',
            borderRadius: '0.75rem',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '0.5rem',
            fontWeight: 500,
          }}
        >
          <span>{actionError}</span>
          <button
            onClick={() => setActionError('')}
            style={{ background: 'transparent', border: 'none', color: '#991b1b', cursor: 'pointer', fontWeight: 700 }}
            aria-label="Dismiss error"
          >
            ✕
          </button>
        </div>
      )}

      {/* Stats Bar */}
      <div className="flex flex-wrap gap-px bg-gray-200">
        {STAT_TILES.map(tile => (
          <div
            key={tile.label}
            className="flex-1 min-w-[33%] sm:min-w-0 bg-white p-4 text-center flex flex-col gap-1"
          >
            <span className={`text-2xl font-bold ${tile.valueClass}`}>{tile.value}</span>
            <span className="text-[11px] text-gray-700 uppercase tracking-wider">{tile.label}</span>
          </div>
        ))}
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-0.5 px-6 pt-3 bg-white border-b border-gray-200 overflow-x-auto">
        {TABS.filter(tab => !tab.requiresReviewLink || reviewLink).map(tab => (
          <button
            key={tab.id}
            className={
              'flex items-center gap-1.5 px-4 py-2.5 bg-transparent text-[13px] font-medium whitespace-nowrap border-b-2 transition-colors ' +
              (activeTab === tab.id
                ? 'text-blue-700 border-blue-500'
                : 'text-gray-700 border-transparent hover:text-slate-800')
            }
            onClick={() => { setActiveTab(tab.id); setStatusFilter(''); setSearchQuery(''); }}
          >
            <tab.icon size={16} />
            {tab.label}
          </button>
        ))}
        <button
          className="hidden sm:flex items-center gap-1.5 ml-auto px-4 py-2.5 text-blue-700 font-semibold text-[13px] border-b-2 border-transparent hover:bg-blue-50 hover:rounded-t-lg whitespace-nowrap"
          onClick={() => setShowEmailCompose(true)}
        >
          <Send size={16} />
          Compose
        </button>
      </div>

      {/* Search & Filter */}
      <div className="px-6 py-3 bg-white border-b border-gray-200">
        <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 max-w-md">
          <Search size={16} className="text-gray-600 shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search by name, phone, email..."
            className="border-0 bg-transparent outline-none flex-1 text-sm text-slate-800 min-w-0"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="bg-transparent border-0 text-gray-600 hover:text-gray-600 p-0.5 flex"
              aria-label="Clear search"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="px-6 pb-6">
        {loading && <div className="text-center py-12 text-gray-700">Loading...</div>}

        {/* LEADS TAB */}
        {activeTab === 'leads' && !loading && (
          <>
            <div className="flex flex-wrap items-center gap-2 py-3">
              {['', 'new', 'contacted', 'qualified', 'converted'].map(s => (
                <button
                  key={s}
                  className={
                    'px-3.5 py-1.5 text-xs rounded-full border transition-colors capitalize ' +
                    (statusFilter === s
                      ? 'border-blue-500 text-blue-700 bg-blue-50'
                      : 'border-gray-300 text-gray-700 bg-white hover:border-gray-400')
                  }
                  onClick={() => setStatusFilter(s)}
                  style={s && statusFilter === s ? { borderColor: STATUS_COLORS[s], color: STATUS_COLORS[s] } : {}}
                >
                  {s || 'All'} {s ? `(${leads.filter(l => l.status === s).length})` : `(${leads.length})`}
                </button>
              ))}
              <button
                type="button"
                onClick={() => downloadAdminFile(
                  statusFilter ? `/leads/export?status=${encodeURIComponent(statusFilter)}` : '/leads/export',
                  'leads.csv',
                )}
                className="ml-auto inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs rounded-full border border-gray-300 text-gray-700 bg-white hover:border-gray-400 transition-colors"
              >
                <Download size={12} /> Export CSV
              </button>
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
              <div className="flex flex-col gap-0.5">
                {filteredLeads.length === 0 && (
                  <div className="text-center py-12 text-gray-600 text-sm">No leads found</div>
                )}
                {filteredLeads.map(lead => (
                  <div
                    key={lead.lead_id}
                    className="flex items-center gap-3 px-4 py-3.5 bg-white rounded-lg cursor-pointer transition hover:bg-white hover:shadow-sm"
                    onClick={() => setSelectedLead(lead)}
                  >
                    <div
                      className="w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-base shrink-0"
                      style={{ background: STATUS_COLORS[lead.status] || '#94a3b8' }}
                    >
                      {(lead.name || '?')[0].toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-semibold text-sm text-slate-800">{lead.name || 'Unknown'}</div>
                      <div className="flex flex-wrap gap-3 mt-1 text-xs text-gray-700">
                        {lead.phone && (
                          <span className="flex items-center gap-1">
                            <Phone size={12} />
                            <a
                              href={`tel:${lead.phone}`}
                              onClick={(e) => e.stopPropagation()}
                              className="hover:text-blue-600 hover:underline"
                            >{lead.phone}</a>
                            <CopyButton value={lead.phone} label="Copy phone" />
                          </span>
                        )}
                        {lead.email && (
                          <span className="flex items-center gap-1">
                            <Mail size={12} />
                            <a
                              href={`mailto:${lead.email}`}
                              onClick={(e) => e.stopPropagation()}
                              className="hover:text-blue-600 hover:underline"
                            >{lead.email}</a>
                            <CopyButton value={lead.email} label="Copy email" />
                          </span>
                        )}
                        {lead.source && (
                          <span className="bg-gray-50 px-2 py-0.5 rounded text-[11px]">{lead.source}</span>
                        )}
                      </div>
                    </div>
                    {reviewLink && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setReviewLead(lead); }}
                        aria-label={`Request review from ${lead.name || 'lead'}`}
                        title="Request a Google review"
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-semibold rounded-full border border-amber-300 text-amber-700 bg-amber-50 hover:bg-amber-100 transition shrink-0"
                      >
                        <Star size={12} />
                        <span className="hidden sm:inline">Request review</span>
                      </button>
                    )}
                    <div className="flex flex-col items-end gap-1.5 shrink-0">
                      <StatusBadge status={lead.status} kind="lead" size="sm" />
                      <span className="text-[11px] text-gray-600">{timeAgo(lead.created_at)}</span>
                    </div>
                    <ChevronRight size={16} className="text-gray-300 shrink-0" />
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* PIPELINE TAB */}
        {activeTab === 'deals' && !loading && (
          <div>
          <NewDealForm onCreated={() => fetchData()} />
          <div className="flex flex-col md:flex-row gap-3 overflow-x-auto py-3">
            {DEAL_STATUS_ORDER.map(status => {
              const statusDeals = filteredDeals.filter(d => d.status === status);
              return (
                <div
                  key={status}
                  className="md:min-w-[240px] flex-1 bg-white rounded-lg flex flex-col"
                >
                  <div
                    className="px-3.5 py-3 border-t-[3px] flex justify-between items-center"
                    style={{ borderTopColor: DEAL_STATUS_COLORS[status] }}
                  >
                    <span className="text-[13px] font-semibold text-slate-800">
                      {status.charAt(0).toUpperCase() + status.slice(1)}
                    </span>
                    <span className="bg-gray-200 text-gray-700 px-2 py-0.5 rounded-full text-xs font-semibold">
                      {statusDeals.length}
                    </span>
                  </div>
                  <div className="p-2 flex flex-col gap-2 min-h-[100px]">
                    {statusDeals.map(deal => (
                      <DealCard
                        key={deal.id}
                        deal={deal}
                        onUpdateStatus={handleUpdateDealStatus}
                        statusOrder={DEAL_STATUS_ORDER}
                      />
                    ))}
                    {statusDeals.length === 0 && (
                      <div className="text-center text-gray-600 text-[13px] py-6">No deals</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          </div>
        )}

        {/* APPOINTMENTS TAB */}
        {activeTab === 'appointments' && !loading && (
          <>
            <div className="flex flex-wrap gap-2 py-3">
              {['', 'confirmed', 'completed', 'cancelled', 'no_show'].map(s => (
                <button
                  key={s}
                  className={
                    'px-3.5 py-1.5 text-xs rounded-full border transition-colors capitalize ' +
                    (statusFilter === s
                      ? 'border-blue-500 text-blue-700 bg-blue-50'
                      : 'border-gray-300 text-gray-700 bg-white hover:border-gray-400')
                  }
                  onClick={() => setStatusFilter(s)}
                >
                  {s || 'All'} ({s ? appointments.filter(a => a.status === s).length : appointments.length})
                </button>
              ))}
            </div>
            <div className="flex flex-col gap-0.5">
              {filteredAppointments.length === 0 && (
                <div className="text-center py-12 text-gray-600 text-sm">No appointments found</div>
              )}
              {filteredAppointments.map(appt => (
                <div
                  key={appt.appointment_id}
                  className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 px-4 py-3.5 bg-white rounded-lg"
                >
                  <div className="flex items-center gap-2 text-gray-700 text-[13px] sm:min-w-[180px]">
                    <Calendar size={16} />
                    <strong>{appt.date}</strong>
                    <span>{appt.time_slot}</span>
                  </div>
                  <div className="flex-1">
                    <div className="font-semibold text-sm text-slate-800">{appt.name}</div>
                    <div className="flex flex-wrap gap-3 text-xs text-gray-700 mt-1">
                      <span className="flex items-center gap-1"><Phone size={12} /> {appt.phone}</span>
                      {appt.email && <span className="flex items-center gap-1"><Mail size={12} /> {appt.email}</span>}
                      {appt.notes && <span className="italic">{appt.notes}</span>}
                    </div>
                  </div>
                  <StatusBadge status={appt.status} kind="appt" size="md" />
                </div>
              ))}
            </div>
          </>
        )}

        {/* TASKS TAB */}
        {activeTab === 'tasks' && !loading && (
          <>
            <div className="py-3">
              <button
                onClick={() => setShowTaskModal(true)}
                className="flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-500 hover:bg-blue-600 text-white border-0 rounded-lg text-sm font-semibold transition-colors"
              >
                + New Task
              </button>
            </div>
            <div className="flex flex-col gap-0.5">
              {tasks.length === 0 && (
                <div className="text-center py-12 text-gray-600 text-sm">No tasks yet</div>
              )}
              {tasks.map(task => {
                const completed = task.status === 'completed';
                const priorityClass =
                  task.priority === 'high'
                    ? 'bg-red-100 text-red-800'
                    : task.priority === 'medium'
                    ? 'bg-amber-100 text-amber-800'
                    : 'bg-green-100 text-green-800';
                return (
                  <div
                    key={task.task_id}
                    className={
                      'flex items-start gap-3 px-4 py-3.5 bg-white rounded-lg border border-transparent transition hover:border-gray-200 hover:shadow-sm ' +
                      (completed ? 'opacity-60 bg-gray-50' : '')
                    }
                  >
                    <div className="pt-0.5">
                      <input
                        type="checkbox"
                        className="w-5 h-5 cursor-pointer accent-blue-500"
                        checked={completed}
                        onChange={() => handleUpdateTaskStatus(task.task_id, completed ? 'pending' : 'completed')}
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div
                        className={
                          'font-semibold text-sm mb-1 ' +
                          (completed ? 'line-through text-gray-600' : 'text-slate-800')
                        }
                      >
                        {task.title}
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700">
                        {task.due_date && (
                          <span
                            className={
                              'flex items-center gap-1 ' +
                              (new Date(task.due_date) < new Date() && !completed ? 'text-red-600 font-semibold' : '')
                            }
                          >
                            <Calendar size={12} /> Due: {formatDate(task.due_date)}
                          </span>
                        )}
                        <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase ${priorityClass}`}>
                          {task.priority}
                        </span>
                        {task.related_lead && (
                          <span className="bg-blue-50 text-blue-800 px-2 py-0.5 rounded">
                            Lead: {leads.find(l => l.lead_id === task.related_lead)?.name || task.related_lead}
                          </span>
                        )}
                      </div>
                      {task.description && (
                        <div className="text-[13px] text-gray-700 mt-1.5 leading-snug">{task.description}</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {/* EMAIL LOG TAB */}
        {activeTab === 'emails' && !loading && (
          <div className="flex flex-col gap-0.5">
            {emails.length === 0 && (
              <div className="text-center py-12 text-gray-600 text-sm">No emails sent yet</div>
            )}
            {emails.map((email, idx) => (
              <div key={idx} className="flex items-center gap-3 px-4 py-3.5 bg-white rounded-lg">
                <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center text-blue-700 shrink-0">
                  <Mail size={16} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-[13px] text-slate-800 truncate">{email.subject}</div>
                  <div className="flex flex-wrap gap-3 text-xs text-gray-700 mt-0.5">
                    <span>To: {email.to}</span>
                    <span className="bg-gray-50 px-2 py-0.5 rounded text-[11px]">{email.email_type}</span>
                  </div>
                </div>
                <div className="text-xs text-gray-600 shrink-0">{timeAgo(email.sent_at)}</div>
              </div>
            ))}
          </div>
        )}

        {activeTab === 'customers' && <CustomerAnalytics />}

        {/* REVIEWS TAB — standalone review-request card (only when link configured) */}
        {activeTab === 'reviews' && reviewLink && (
          <div className="py-4">
            <ReviewRequestCard reviewLink={reviewLink} />
          </div>
        )}
      </div>

      {/* Review Request Modal (per-lead) */}
      {reviewLead && reviewLink && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-6"
          onClick={() => setReviewLead(null)}
        >
          <div className="w-full max-w-xl" onClick={e => e.stopPropagation()}>
            <ReviewRequestCard
              reviewLink={reviewLink}
              lead={reviewLead}
              onClose={() => setReviewLead(null)}
            />
          </div>
        </div>
      )}

      {/* Email Compose Modal */}
      {showEmailCompose && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-6"
          onClick={() => { setShowEmailCompose(false); setEmailResult(null); }}
        >
          <div
            className="bg-white rounded-2xl w-full max-w-[540px] shadow-2xl overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex justify-between items-center px-6 py-4 border-b border-gray-200">
              <h3 className="m-0 text-[17px] font-bold text-slate-800">Compose Email</h3>
              <button
                onClick={() => { setShowEmailCompose(false); setEmailResult(null); }}
                className="bg-transparent border-0 text-gray-700 hover:text-gray-700 p-1 cursor-pointer"
                aria-label="Close"
              >
                <X size={20} />
              </button>
            </div>
            <form onSubmit={handleSendEmail} className="px-6 py-6 flex flex-col gap-3.5">
              {/* Template Selector */}
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wide">
                  Template
                </label>
                <select
                  value={selectedTemplate}
                  onChange={e => applyEmailTemplate(e.target.value)}
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 bg-white outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition"
                >
                  <option value="">Select a template...</option>
                  {EMAIL_TEMPLATES.map(t => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wide">To</label>
                <input
                  type="email"
                  value={emailForm.to}
                  onChange={e => setEmailForm(f => ({ ...f, to: e.target.value }))}
                  placeholder="customer@email.com"
                  required
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition box-border"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wide">Name</label>
                <input
                  type="text"
                  value={emailForm.customer_name}
                  onChange={e => setEmailForm(f => ({ ...f, customer_name: e.target.value }))}
                  placeholder="Customer name"
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition box-border"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wide">Subject</label>
                <input
                  type="text"
                  value={emailForm.subject}
                  onChange={e => setEmailForm(f => ({ ...f, subject: e.target.value }))}
                  placeholder="Email subject"
                  required
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition box-border"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wide">Message</label>
                <textarea
                  value={emailForm.message}
                  onChange={e => setEmailForm(f => ({ ...f, message: e.target.value }))}
                  placeholder="Write your message..."
                  rows={8}
                  required
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition box-border resize-y font-inherit"
                />
              </div>
              {emailResult && (
                <div
                  className={
                    'flex items-center gap-2 px-3.5 py-2.5 rounded-lg text-[13px] ' +
                    (emailResult.success ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800')
                  }
                >
                  {emailResult.success ? (
                    <><CheckCircle size={16} /> Email sent successfully</>
                  ) : (
                    <><AlertCircle size={16} /> {emailResult.error || 'Send failed'}</>
                  )}
                </div>
              )}
              <button
                type="submit"
                disabled={emailSending}
                className="flex items-center justify-center gap-2 px-3 py-3 bg-blue-500 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white border-0 rounded-lg text-sm font-semibold cursor-pointer transition-colors"
              >
                <Send size={16} />
                {emailSending ? 'Sending...' : 'Send Email'}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* Task Modal */}
      {showTaskModal && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-6"
          onClick={() => setShowTaskModal(false)}
        >
          <div
            className="bg-white rounded-2xl w-full max-w-[480px] shadow-2xl overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex justify-between items-center px-6 py-4 border-b border-gray-200">
              <h3 className="m-0 text-[17px] font-bold text-slate-800">Create New Task</h3>
              <button
                onClick={() => setShowTaskModal(false)}
                className="bg-transparent border-0 text-gray-700 hover:text-gray-700 p-1 cursor-pointer"
                aria-label="Close"
              >
                <X size={20} />
              </button>
            </div>
            <form onSubmit={handleCreateTask} className="px-6 py-6 flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Title *</label>
                <input
                  type="text"
                  value={taskForm.title}
                  onChange={e => setTaskForm(f => ({ ...f, title: e.target.value }))}
                  placeholder="e.g., Follow up with John about financing"
                  required
                  className="px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Description</label>
                <textarea
                  value={taskForm.description}
                  onChange={e => setTaskForm(f => ({ ...f, description: e.target.value }))}
                  placeholder="Additional details..."
                  rows={3}
                  className="px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition resize-y min-h-[80px] font-inherit"
                />
              </div>
              <div className="flex gap-4">
                <div className="flex flex-col gap-1.5 flex-1">
                  <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Due Date</label>
                  <input
                    type="date"
                    value={taskForm.due_date}
                    onChange={e => setTaskForm(f => ({ ...f, due_date: e.target.value }))}
                    className="px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition"
                  />
                </div>
                <div className="flex flex-col gap-1.5 flex-1">
                  <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Priority</label>
                  <select
                    value={taskForm.priority}
                    onChange={e => setTaskForm(f => ({ ...f, priority: e.target.value }))}
                    className="px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
                  Related Lead (optional)
                </label>
                <select
                  value={taskForm.related_lead}
                  onChange={e => setTaskForm(f => ({ ...f, related_lead: e.target.value }))}
                  className="px-3 py-2.5 border border-gray-300 rounded-lg text-sm text-slate-800 outline-none bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 transition"
                >
                  <option value="">None</option>
                  {leads.map(l => (
                    <option key={l.lead_id} value={l.lead_id}>{l.name || l.email || l.phone}</option>
                  ))}
                </select>
              </div>
              <button
                type="submit"
                className="flex items-center justify-center gap-2 px-3 py-3 mt-2 bg-blue-500 hover:bg-blue-600 text-white border-0 rounded-lg text-sm font-semibold cursor-pointer transition-colors"
              >
                Create Task
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
    <div className="bg-white rounded-xl p-6">
      <button
        onClick={onClose}
        className="flex items-center gap-1.5 bg-transparent border-0 text-blue-700 hover:text-blue-600 text-[13px] cursor-pointer p-0 mb-5"
      >
        <ArrowLeft size={16} /> Back to leads
      </button>

      <div className="flex items-center gap-4 mb-6">
        <div
          className="w-14 h-14 rounded-full flex items-center justify-center text-white font-bold text-[22px] shrink-0"
          style={{ background: STATUS_COLORS[lead.status] || '#94a3b8' }}
        >
          {(lead.name || '?')[0].toUpperCase()}
        </div>
        <div>
          <h2 className="text-xl font-bold text-slate-800 m-0">{lead.name || 'Unknown'}</h2>
          <div className="flex flex-wrap gap-4 mt-1 text-[13px] text-gray-700">
            {lead.phone && <span className="flex items-center gap-1"><Phone size={14} /> {lead.phone}</span>}
            {lead.email && <span className="flex items-center gap-1"><Mail size={14} /> {lead.email}</span>}
          </div>
        </div>
      </div>

      {/* Status Actions */}
      <div className="mb-5 pb-5 border-b border-gray-200">
        <h3 className="text-xs uppercase tracking-wide text-gray-700 m-0 mb-2.5 font-semibold">Status</h3>
        <div className="flex flex-wrap gap-2">
          {['new', 'contacted', 'qualified', 'converted'].map(s => (
            <button
              key={s}
              className={
                'px-4 py-1.5 rounded-lg text-[13px] cursor-pointer capitalize transition ' +
                (lead.status === s
                  ? 'border border-transparent text-white'
                  : 'border border-gray-300 bg-white hover:border-gray-400 text-gray-700')
              }
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
        <div className="mb-5 pb-5 border-b border-gray-200">
          <h3 className="text-xs uppercase tracking-wide text-gray-700 m-0 mb-2.5 font-semibold">Preferences</h3>
          <div className="flex flex-wrap gap-4 text-[13px] text-gray-700">
            {lead.bedrooms && <span className="flex items-center gap-1"><Home size={14} /> {lead.bedrooms} bed</span>}
            {lead.bathrooms && <span className="flex items-center gap-1">{lead.bathrooms} bath</span>}
            {lead.budget_max && (
              <span className="flex items-center gap-1">
                <DollarSign size={14} />
                {Number(lead.budget_max).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
              </span>
            )}
            {lead.home_type && <span className="flex items-center gap-1">{lead.home_type}</span>}
          </div>
        </div>
      )}

      {/* Engagement */}
      <div className="mb-5 pb-5 border-b border-gray-200">
        <h3 className="text-xs uppercase tracking-wide text-gray-700 m-0 mb-2.5 font-semibold">Engagement</h3>
        <div className="flex flex-wrap gap-4 text-[13px] text-gray-700">
          <span className="flex items-center gap-1">Source: <strong>{lead.source}</strong></span>
          <span className="flex items-center gap-1">Appointment: <strong>{lead.appointment_requested ? 'Yes' : 'No'}</strong></span>
          <span className="flex items-center gap-1">Financing: <strong>{lead.financing_discussed ? 'Discussed' : 'No'}</strong></span>
          {lead.homes_viewed?.length > 0 && (
            <span className="flex items-center gap-1">Viewed: <strong>{lead.homes_viewed.join(', ')}</strong></span>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="mb-5 pb-5 border-b border-gray-200">
        <h3 className="text-xs uppercase tracking-wide text-gray-700 m-0 mb-2.5 font-semibold">Actions</h3>
        <div className="flex flex-col sm:flex-row gap-2.5">
          {lead.phone && (
            <a
              href={`tel:${lead.phone}`}
              className="flex items-center justify-center gap-1.5 px-5 py-2.5 rounded-lg text-sm font-medium border-0 bg-green-500 hover:bg-green-600 text-white no-underline transition-colors"
            >
              <Phone size={16} /> Call
            </a>
          )}
          {lead.email && (
            <button
              onClick={() => onEmail(lead)}
              className="flex items-center justify-center gap-1.5 px-5 py-2.5 rounded-lg text-sm font-medium border-0 bg-blue-500 hover:bg-blue-600 text-white cursor-pointer transition-colors"
            >
              <Mail size={16} /> Email
            </button>
          )}
        </div>
      </div>

      {/* Activity Timeline */}
      <div className="mb-5 pb-5 border-b border-gray-200">
        <h3 className="text-xs uppercase tracking-wide text-gray-700 m-0 mb-2.5 font-semibold">Activity</h3>
        <div className="flex flex-col pl-3">
          {/* Created */}
          <div className="flex gap-3 py-2.5 pl-4 border-l-2 border-gray-200 relative">
            <div
              className="absolute -left-1.5 top-3.5 w-2.5 h-2.5 rounded-full border-2 border-white"
              style={{ background: '#3b82f6' }}
            />
            <div className="flex flex-col gap-0.5">
              <span className="text-[13px] text-gray-700">Lead created via {lead.source}</span>
              {lead.created_at && (
                <span className="text-[11px] text-gray-600">{formatDate(lead.created_at)}</span>
              )}
            </div>
          </div>

          {/* Related Appointments */}
          {appointments.map(appt => (
            <div
              key={appt.appointment_id}
              className="flex gap-3 py-2.5 pl-4 border-l-2 border-gray-200 relative"
            >
              <div
                className="absolute -left-1.5 top-3.5 w-2.5 h-2.5 rounded-full border-2 border-white"
                style={{ background: '#22c55e' }}
              />
              <div className="flex flex-col gap-0.5">
                <span className="text-[13px] text-gray-700">
                  Appointment {appt.status} — {appt.date} at {appt.time_slot}
                </span>
                {appt.created_at && (
                  <span className="text-[11px] text-gray-600">{formatDate(appt.created_at)}</span>
                )}
              </div>
            </div>
          ))}

          {/* Related Emails */}
          {emails.map((email, idx) => (
            <div key={idx} className="flex gap-3 py-2.5 pl-4 border-l-2 border-gray-200 relative">
              <div
                className="absolute -left-1.5 top-3.5 w-2.5 h-2.5 rounded-full border-2 border-white"
                style={{ background: '#f59e0b' }}
              />
              <div className="flex flex-col gap-0.5">
                <span className="text-[13px] text-gray-700">Email sent: {email.subject}</span>
                {email.sent_at && (
                  <span className="text-[11px] text-gray-600">{formatDate(email.sent_at)}</span>
                )}
              </div>
            </div>
          ))}

          {appointments.length === 0 && emails.length === 0 && (
            <div className="text-[13px] text-gray-600 py-2">No activity yet beyond lead creation</div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-4 text-[11px] text-gray-600 mt-4">
        {lead.lead_id && <span>ID: {lead.lead_id}</span>}
        {lead.created_at && <span>Created: {formatDate(lead.created_at)}</span>}
        {lead.updated_at && <span>Updated: {formatDate(lead.updated_at)}</span>}
      </div>
    </div>
  );
}

// ─── New Deal Form ──────────────────────────────────

function NewDealForm({ onCreated }) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setResult(null);
    const fd = new FormData(e.target);
    const data = {};
    for (const [k, v] of fd.entries()) {
      if (v.trim()) data[k] = k === 'sales_price' || k === 'down_payment' ? parseFloat(v) : v.trim();
    }

    try {
      const res = await adminFetch('/api/deals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const d = await res.json();
      if (d.success || d.deal_id) {
        setResult({ ok: true, msg: `Deal created for ${data.buyer_first_name} ${data.buyer_last_name}` });
        e.target.reset();
        onCreated();
        setTimeout(() => { setOpen(false); setResult(null); }, 2000);
      } else {
        setResult({ ok: false, msg: d.error || 'Failed to create deal' });
      }
    } catch {
      setResult({ ok: false, msg: 'Network error' });
    }
    setSaving(false);
  };

  if (!open) {
    return (
      <div style={{ marginBottom: 12 }}>
        <button onClick={() => setOpen(true)}
          style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 20px', background: '#22c55e',
                   color: '#000', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>
          + New Deal
        </button>
      </div>
    );
  }

  const field = (label, name, opts = {}) => (
    <div style={{ flex: opts.half ? '1 1 45%' : '1 1 100%' }}>
      <label style={{ fontSize: 10, color: '#888', textTransform: 'uppercase', letterSpacing: 1 }}>{label}</label>
      <input name={name} type={opts.type || 'text'} required={opts.req} placeholder={opts.ph || ''}
        style={{ width: '100%', padding: '8px 10px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
                 borderRadius: 6, color: '#fff', fontSize: 13, marginTop: 2 }} />
    </div>
  );

  return (
    <div style={{ background: '#111118', border: '1px solid #22c55e33', borderRadius: 10, padding: 16, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: '#22c55e' }}>New Deal</span>
        <button onClick={() => setOpen(false)} style={{ background: 'none', border: 'none', color: '#666', cursor: 'pointer', fontSize: 18 }}>×</button>
      </div>
      <form onSubmit={handleSubmit}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
          {field('First Name', 'buyer_first_name', { half: true, req: true, ph: 'John' })}
          {field('Last Name', 'buyer_last_name', { half: true, req: true, ph: 'Smith' })}
          {field('Phone', 'buyer_phone', { half: true, ph: '555-123-4567' })}
          {field('Email', 'buyer_email', { half: true, type: 'email', ph: 'john@example.com' })}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
          {field('Address', 'buyer_address', { ph: '123 Main St' })}
          {field('City', 'buyer_city', { half: true, ph: 'Houston' })}
          {field('County', 'buyer_county', { half: true, ph: 'Harris' })}
          {field('State', 'buyer_state', { half: true, ph: 'TX' })}
          {field('Zip', 'buyer_zip', { half: true, ph: '77001' })}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
          {field('Manufacturer', 'manufacturer', { half: true, ph: 'Clayton Homes' })}
          {field('Model', 'model', { half: true, ph: 'The Breeze' })}
          {field('Serial #', 'serial_number_1', { half: true, ph: 'CLT-2026-001' })}
          {field('Sales Rep', 'salesrep', { half: true, ph: 'Adriana Squyres' })}
          {field('Sales Price', 'sales_price', { half: true, type: 'number', ph: '72000' })}
          {field('Down Payment', 'down_payment', { half: true, type: 'number', ph: '8000' })}
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button type="submit" disabled={saving}
            style={{ padding: '10px 24px', background: '#22c55e', color: '#000', border: 'none',
                     borderRadius: 8, cursor: 'pointer', fontWeight: 700, fontSize: 13, opacity: saving ? 0.5 : 1 }}>
            {saving ? 'Creating...' : 'Create Deal'}
          </button>
          {result && (
            <span style={{ fontSize: 12, color: result.ok ? '#22c55e' : '#ef4444' }}>
              {result.ok ? '✅' : '❌'} {result.msg}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}


// ─── Customer Analytics Panel ────────────────────────

function FunnelPanel() {
  const [funnel, setFunnel] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminFetch('/api/admin/crm/funnel')
      .then(r => r.json())
      .then(d => { if (d?.success) setFunnel(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-center py-6 text-gray-600 text-sm">Loading funnel...</div>;
  if (!funnel?.stages?.length) return null;

  const top = funnel.stages[0]?.count || 1;
  const stageColors = {
    LEAD: '#f59e0b',
    ENROLLED: '#3b82f6',
    DEAL: '#8b5cf6',
    CLOSED: '#22c55e',
  };

  return (
    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, fontWeight: 600 }}>
          CRM Funnel
        </div>
        <div style={{ fontSize: 10, color: '#666' }}>cached 5m</div>
      </div>
      {funnel.stages.map(stage => {
        const widthPct = top ? Math.max(2, (stage.count / top) * 100) : 0;
        return (
          <div key={stage.key} style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
              <span style={{ color: '#fff', fontWeight: 600 }}>{stage.label}</span>
              <span style={{ color: '#aaa' }}>
                {stage.count.toLocaleString()} <span style={{ color: '#666' }}>({stage.conversion_pct}%)</span>
              </span>
            </div>
            <div style={{ height: 18, background: 'rgba(255,255,255,0.04)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{
                width: `${widthPct}%`,
                height: '100%',
                background: stageColors[stage.key] || '#3b82f6',
                transition: 'width 0.4s ease',
              }} />
            </div>
          </div>
        );
      })}
      {(funnel.median_days_in_stage?.LEAD_to_ENROLLED != null ||
        funnel.median_days_in_stage?.DEAL_to_CLOSED != null) && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.05)', display: 'flex', gap: 24, fontSize: 11, color: '#888' }}>
          {funnel.median_days_in_stage.LEAD_to_ENROLLED != null && (
            <span>Median Lead to Enrolled: <strong style={{ color: '#fff' }}>{funnel.median_days_in_stage.LEAD_to_ENROLLED}d</strong></span>
          )}
          {funnel.median_days_in_stage.DEAL_to_CLOSED != null && (
            <span>Median Deal to Closed: <strong style={{ color: '#fff' }}>{funnel.median_days_in_stage.DEAL_to_CLOSED}d</strong></span>
          )}
        </div>
      )}
    </div>
  );
}


// Stable color palette for lead source categories
const SOURCE_COLORS = [
  '#3b82f6', '#22c55e', '#f59e0b', '#8b5cf6', '#ec4899',
  '#14b8a6', '#ef4444', '#a3e635', '#06b6d4', '#f97316',
];

function LeadSourceChart() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    adminFetch(`/api/admin/crm/lead-sources?days=${days}`)
      .then(r => r.json())
      .then(d => { if (alive && d?.success) setData(d); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [days]);

  if (loading) return <div className="text-center py-4 text-gray-600 text-xs">Loading lead sources...</div>;
  if (!data?.categories?.length) return null;

  const chartData = data.categories.map((c, i) => ({
    name: c.category,
    value: c.count,
    color: SOURCE_COLORS[i % SOURCE_COLORS.length],
    revenue: c.attributed_revenue,
    deals: c.attributed_deals,
    pct: c.pct,
  }));

  const totalRevenue = chartData.reduce((s, c) => s + (c.revenue || 0), 0);

  return (
    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#aaa', textTransform: 'uppercase', letterSpacing: 1, fontWeight: 600 }}>
          Lead Sources ({data.total_leads} leads)
        </div>
        <select
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', borderRadius: 6, padding: '4px 8px', fontSize: 11 }}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
          <option value={365}>Last 365 days</option>
        </select>
      </div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ width: 220, height: 220, flexShrink: 0 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                dataKey="value"
                nameKey="name"
                innerRadius={50}
                outerRadius={90}
                paddingAngle={2}
              >
                {chartData.map((entry, idx) => (
                  <Cell key={idx} fill={entry.color} stroke="rgba(0,0,0,0.4)" />
                ))}
              </Pie>
              <RechartsTooltip
                contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 6, fontSize: 12 }}
                formatter={(value, _name, item) => [
                  `${value} leads (${item.payload.pct}%)`,
                  item.payload.name,
                ]}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          {chartData.map((c) => (
            <div key={c.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 10, height: 10, background: c.color, borderRadius: 2, display: 'inline-block' }} />
                <span style={{ color: '#fff', fontWeight: 500 }}>{c.name}</span>
              </div>
              <div style={{ display: 'flex', gap: 12, color: '#aaa' }}>
                <span>{c.value} ({c.pct}%)</span>
                {c.revenue > 0 && (
                  <span style={{ color: '#22c55e', fontWeight: 500 }}>
                    ${c.revenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                )}
              </div>
            </div>
          ))}
          {totalRevenue > 0 && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(255,255,255,0.08)', fontSize: 11, color: '#888', textAlign: 'right' }}>
              Total attributed revenue: <strong style={{ color: '#22c55e' }}>${totalRevenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function CustomerAnalytics() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [searchQ, setSearchQ] = useState('');
  const [results, setResults] = useState([]);

  useEffect(() => {
    adminFetch('/api/analytics/customers').then(r => r.json()).then(setData).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const handleSearch = async () => {
    if (!searchQ.trim()) return;
    try {
      const r = await adminFetch(`/api/customers/search?q=${encodeURIComponent(searchQ)}&limit=20`);
      const d = await r.json();
      setResults(d.customers || []);
    } catch { setResults([]); }
  };

  if (loading) return <div className="text-center py-12 text-gray-600 text-sm">Loading customer analytics...</div>;
  if (!data) return <div className="text-center py-12 text-gray-600 text-sm">Failed to load analytics</div>;

  return (
    <div style={{ padding: '0 4px' }}>
      {/* Funnel panel — counts + conversion + median time-in-stage */}
      <FunnelPanel />

      {/* Lead source attribution donut chart */}
      <LeadSourceChart />

      {/* Metrics bar */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        {[
          { label: 'Total Customers', value: data.total?.toLocaleString(), color: '#00aaff' },
          { label: 'Enrolled', value: data.by_status?.ENROLLED?.toLocaleString(), color: '#22c55e' },
          { label: 'Leads', value: data.by_status?.LEAD?.toLocaleString(), color: '#f59e0b' },
          { label: 'Conversion', value: `${data.conversion_rate}%`, color: '#8b5cf6' },
        ].map(m => (
          <div key={m.label} style={{ flex: '1 1 120px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '12px 16px' }}>
            <div style={{ fontSize: 10, color: '#888', textTransform: 'uppercase', letterSpacing: 1 }}>{m.label}</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: m.color, marginTop: 4 }}>{m.value}</div>
          </div>
        ))}
      </div>

      {/* Search */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          placeholder="Search customers by name, phone, email..."
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          style={{ flex: 1, padding: '10px 14px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#fff', fontSize: 13 }}
        />
        <button onClick={handleSearch} style={{ padding: '10px 20px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600 }}>
          <Search size={14} /> Search
        </button>
      </div>

      {/* Search results */}
      {results.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>
            {results.length} results
          </div>
          {results.map((c, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)', fontSize: 13 }}>
              <div>
                <span style={{ color: '#fff', fontWeight: 600 }}>{c.full_name}</span>
                {c.phone && <span style={{ color: '#666', marginLeft: 12 }}>{c.phone}</span>}
              </div>
              <span style={{ color: c.status === 'ENROLLED' ? '#22c55e' : c.status === 'LEAD' ? '#f59e0b' : '#888', fontSize: 11, fontWeight: 600 }}>{c.status}</span>
            </div>
          ))}
        </div>
      )}

      {/* Top cities + salesreps */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: 8, padding: 14, border: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: 11, color: '#888', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Top Cities</div>
          {(data.top_cities || []).slice(0, 8).map((c, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 12 }}>
              <span style={{ color: '#ccc' }}>{c.city}</span>
              <span style={{ color: '#666' }}>{c.count}</span>
            </div>
          ))}
        </div>
        <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: 8, padding: 14, border: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: 11, color: '#888', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Sales Reps</div>
          {(data.top_salesreps || []).slice(0, 8).map((r, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 12 }}>
              <span style={{ color: '#ccc' }}>{r.name}</span>
              <span style={{ color: '#666' }}>{r.count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}


// ─── Deal Card (Pipeline) ───────────────────────────

function DealCard({ deal, onUpdateStatus, statusOrder }) {
  const [expanded, setExpanded] = useState(false);
  const [generating, setGenerating] = useState(null); // 'doc' | 'packet' | null
  const [genResult, setGenResult] = useState(null);
  const [signing, setSigning] = useState(false);
  const [signResult, setSignResult] = useState(null);
  const currentIdx = statusOrder.indexOf(deal.status);
  const nextStatus = currentIdx >= 0 && currentIdx < statusOrder.length - 1 ? statusOrder[currentIdx + 1] : null;
  const buyerName = `${deal.buyer_first_name || ''} ${deal.buyer_last_name || ''}`.trim();
  // Display label: prefer the server-enriched `home_label` (joined from
  // inventory by serial when the deal lacks model info), then any model
  // field already on the deal record, then a Serial fallback so the user
  // can still identify the home, then the empty-state copy.
  const dealSerial = deal.serial_number_1 || deal.serial_number || null;
  const homeLabel = deal.home_label || deal.model || deal.home_model || (dealSerial ? `Serial: ${dealSerial}` : 'No home selected');
  const homeManufacturer = deal.home_manufacturer || deal.manufacturer || null;
  const detailManufacturerModel = [homeManufacturer, deal.home_label || deal.model || deal.home_model]
    .filter(Boolean)
    .join(' ');

  const handleGenerateDoc = async (templateName) => {
    setGenerating('doc');
    setGenResult(null);
    try {
      const res = await adminFetch(`/api/deals/${deal.id}/generate-document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: templateName }),
      });
      const data = await res.json();
      setGenResult(data);
    } catch {
      setGenResult({ success: false, error: 'Generation failed' });
    }
    setGenerating(null);
  };

  const handleGeneratePacket = async () => {
    setGenerating('packet');
    setGenResult(null);
    try {
      const res = await adminFetch(`/api/deals/${deal.id}/generate-packet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ packet_name: 'standard_closing' }),
      });
      const data = await res.json();
      setGenResult(data);
    } catch {
      setGenResult({ success: false, error: 'Packet generation failed' });
    }
    setGenerating(null);
  };

  const handleDownload = async (url) => {
    try {
      await downloadAdminFile(url, url.split('/').pop());
    } catch (err) {
      setGenResult({ success: false, error: err.message || 'Download failed' });
    }
  };

  const handleSendForSignature = async () => {
    const signerEmail = deal.buyer_email;
    const signerName = `${deal.buyer_first_name || ''} ${deal.buyer_last_name || ''}`.trim();
    if (!signerEmail) {
      setSignResult({ success: false, error: 'No buyer email on this deal.' });
      return;
    }
    setSigning(true);
    setSignResult(null);
    try {
      const res = await adminFetch('/api/docuseal/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          deal_id: deal.id,
          template_name: 'TMHA_SalesContract.pdf',
          signer_email: signerEmail,
          signer_name: signerName || signerEmail,
        }),
      });
      const data = await res.json();
      if (res.status === 501) {
        setSignResult({ success: false, comingSoon: true, message: data.message || 'DocuSeal not yet configured.' });
      } else {
        setSignResult(data);
      }
    } catch {
      setSignResult({ success: false, error: 'Request failed. Please try again.' });
    }
    setSigning(false);
  };

  return (
    <div className="bg-white rounded-lg p-3 shadow-sm">
      <div className="flex justify-between items-start cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <div>
          <div className="font-semibold text-[13px] text-slate-800">{buyerName || 'No Name'}</div>
          <div className="text-xs text-gray-700 mt-0.5">{homeLabel}</div>
        </div>
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </div>

      {deal.sales_price && (
        <div className="text-[15px] font-bold text-emerald-600 mt-2">
          ${Number(deal.sales_price).toLocaleString()}
        </div>
      )}

      {expanded && (
        <div className="mt-2.5 pt-2.5 border-t border-gray-200 text-xs text-gray-700 flex flex-col gap-1">
          {deal.buyer_phone && <div className="flex items-center gap-1"><Phone size={12} /> {deal.buyer_phone}</div>}
          {deal.buyer_email && <div className="flex items-center gap-1"><Mail size={12} /> {deal.buyer_email}</div>}
          {deal.salesrep && <div className="flex items-center gap-1">Rep: {deal.salesrep}</div>}
          {detailManufacturerModel && <div className="flex items-center gap-1">{detailManufacturerModel}</div>}
          {dealSerial && <div className="flex items-center gap-1">S/N: {dealSerial}</div>}
          {deal.buyer_address && (
            <div className="flex items-center gap-1">
              {deal.buyer_address}, {deal.buyer_city} {deal.buyer_state} {deal.buyer_zip}
            </div>
          )}
          {deal.created_at && (
            <div className="text-gray-600 mt-1">Created: {formatDate(deal.created_at)}</div>
          )}

          {/* Document Generation Actions */}
          <div className="mt-2.5 text-[11px] text-gray-700 font-semibold uppercase tracking-wide">
            Generate Documents
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <button
              onClick={() => handleGenerateDoc('TMHA_SalesContract.pdf')}
              disabled={generating !== null}
              style={{ background: '#1a1a2e', border: '1px solid #3b82f6', color: '#3b82f6' }}
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-md cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {generating === 'doc' ? <Loader size={12} className="animate-spin" /> : <FileText size={12} />}
              Sales Contract
            </button>
            {[
              { template: 'TDHCA_1038_Consumer_Disclosure.pdf', label: 'Consumer Disclosure', color: '#f59e0b' },
              { template: 'TDHCA_1054_Habitability_Warranty.pdf', label: 'Warranty', color: '#8b5cf6' },
              { template: 'Internal_Homestead.pdf', label: 'Homestead', color: '#ec4899' },
            ].map(({ template, label, color }) => (
              <button
                key={template}
                onClick={() => handleGenerateDoc(template)}
                disabled={generating !== null}
                style={{ background: '#1a1a2e', border: `1px solid ${color}`, color }}
                className="flex items-center gap-1 px-2.5 py-1.5 text-[11px] rounded-md cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <FileText size={11} /> {label}
              </button>
            ))}
            <button
              onClick={handleGeneratePacket}
              disabled={generating !== null}
              style={{ background: '#1a1a2e', border: '1px solid #22c55e', color: '#22c55e' }}
              className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-md cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {generating === 'packet' ? <Loader size={12} className="animate-spin" /> : <Package size={12} />}
              Full Closing Packet (9 docs)
            </button>
          </div>

          {/* E-Sign */}
          <div style={{ marginTop: '8px', display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            <button
              onClick={handleSendForSignature}
              disabled={signing || generating !== null}
              style={{ display: 'flex', alignItems: 'center', gap: '4px', padding: '6px 12px', fontSize: '12px',
                       background: '#1a1a2e', border: '1px solid #6366f1', color: '#6366f1', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
            >
              {signing ? <Loader size={12} className="spin" /> : <Send size={12} />}
              Send for Signature
            </button>
          </div>

          {signResult && (
            <div style={{ marginTop: '6px', padding: '8px', borderRadius: '6px', fontSize: '12px',
                          background: signResult.comingSoon ? 'rgba(99,102,241,0.1)' : signResult.success ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                          border: `1px solid ${signResult.comingSoon ? '#6366f1' : signResult.success ? '#22c55e' : '#ef4444'}` }}>
              {signResult.comingSoon ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#818cf8' }}>
                  <Send size={13} />
                  <span>Coming soon — {signResult.message}</span>
                </div>
              ) : signResult.success ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#22c55e' }}>
                  <CheckCircle size={13} />
                  <span>Signing request sent to {deal.buyer_email}</span>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#ef4444' }}>
                  <AlertCircle size={13} />
                  <span>{signResult.error || 'Send failed'}</span>
                </div>
              )}
            </div>
          )}

          {/* Generation Result */}
          {genResult && (
            <div
              className={
                'mt-2 p-2 rounded-md text-xs border ' +
                (genResult.success
                  ? 'bg-green-500/10 border-green-500'
                  : 'bg-red-500/10 border-red-500')
              }
            >
              {genResult.success ? (
                <div className="flex items-center gap-1.5">
                  <CheckCircle size={14} className="text-green-700" />
                  <span>{genResult.message}</span>
                  <button
                    onClick={() => handleDownload(genResult.download_url)}
                    className="ml-auto flex items-center gap-1 px-2 py-1 bg-green-700 text-white border-0 rounded text-[11px] font-semibold cursor-pointer"
                  >
                    <Download size={12} /> Download
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-red-500">
                  <AlertCircle size={14} /> {genResult.error || 'Generation failed'}
                </div>
              )}
              {genResult.documents_included && (
                <div className="mt-1.5 text-gray-700 text-[11px]">
                  {genResult.page_count} pages • {genResult.documents_included.length} documents
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {nextStatus && (
        <button
          className="mt-2.5 w-full px-2 py-1.5 border border-dashed border-gray-300 hover:border-blue-500 hover:bg-blue-50 bg-transparent rounded-md text-xs text-blue-700 cursor-pointer transition-colors"
          onClick={() => onUpdateStatus(deal.id, nextStatus)}
        >
          Move to {nextStatus} →
        </button>
      )}
    </div>
  );
}
