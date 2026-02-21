import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  FileText, ArrowLeft, Download, CheckCircle, AlertCircle, Search, Package,
  Loader2, Plus, ChevronRight, Edit3, Archive, Eye, X, Users, Home,
  DollarSign, Briefcase, MapPin, Phone, Mail, ClipboardList
} from 'lucide-react';
import SmartForm from '../components/SmartForm';

// ─── Constants ───────────────────────────────────────────────────────
const CATEGORY_ORDER = ['TMHA', 'TDHCA', 'State', 'Internal'];
const CATEGORY_COLORS = {
  TMHA: 'bg-blue-100 text-blue-700',
  TDHCA: 'bg-green-100 text-green-700',
  State: 'bg-yellow-100 text-yellow-700',
  Internal: 'bg-purple-100 text-purple-700',
};

const STATUS_CONFIG = {
  pending:   { label: 'Pending',   color: 'bg-yellow-100 text-yellow-800 border-yellow-300' },
  approved:  { label: 'Approved',  color: 'bg-blue-100 text-blue-800 border-blue-300' },
  contract:  { label: 'Contract',  color: 'bg-purple-100 text-purple-800 border-purple-300' },
  funded:    { label: 'Funded',    color: 'bg-green-100 text-green-800 border-green-300' },
  complete:  { label: 'Complete',  color: 'bg-emerald-100 text-emerald-800 border-emerald-300' },
  denied:    { label: 'Denied',    color: 'bg-red-100 text-red-800 border-red-300' },
  archived:  { label: 'Archived',  color: 'bg-gray-100 text-gray-600 border-gray-300' },
};

const STATUS_FLOW = ['pending', 'approved', 'contract', 'funded', 'complete'];

const EMPTY_DEAL = {
  salesrep: '', status: 'pending',
  buyer_first_name: '', buyer_last_name: '', buyer_phone: '', buyer_email: '',
  buyer_ssn: '', buyer_marital_status: '',
  co_buyer_first_name: '', co_buyer_last_name: '', co_buyer_phone: '',
  co_buyer_ssn: '', co_buyer_marital_status: '',
  employer_name: '', occupation: '', occupation_length: '', work_phone: '',
  self_employed: false, previous_employer: '', previous_occupation: '',
  co_buyer_employer: '', co_buyer_occupation: '', co_buyer_occupation_length: '',
  co_buyer_work_phone: '', co_buyer_self_employed: false,
  mailing_address: '', mailing_city: '', mailing_state: 'TX', mailing_zip: '',
  mailing_length: '', mailing_own_rent: '',
  buyer_address: '', buyer_city: '', buyer_county: '', buyer_state: 'TX', buyer_zip: '',
  reference1_name: '', reference1_phone: '', reference2_name: '', reference2_phone: '',
  manufacturer: '', model: '', year: '', serial_number_1: '', serial_number_2: '',
  label_number_1: '', label_number_2: '', no_of_sections: '', is_new: true,
  inventory_id: '',
  sales_price: '', down_payment: '',
  creditor_name: '', creditor_address: '', creditor_city_state_zip: '', creditor_phone: '',
  loan_term: '', apr: '', finance_charge: '', max_financed: '', total_payments: '',
  payment_start_date: '', insurance_premium: '',
  notes: '',
};

// ─── Helpers ─────────────────────────────────────────────────────────
const StatusBadge = ({ status }) => {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${cfg.color}`}>
      {cfg.label}
    </span>
  );
};

const formatDate = (d) => {
  if (!d) return '';
  try {
    const dt = d.seconds ? new Date(d.seconds * 1000) : new Date(d);
    return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch { return ''; }
};

const buyerDisplayName = (deal) => {
  const parts = [deal.buyer_first_name, deal.buyer_last_name].filter(Boolean);
  return parts.length > 0 ? parts.join(' ') : 'Unnamed Deal';
};

// ═══════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════
const DocumentCenter = ({ onBack, sessionId }) => {
  // Top-level tab: 'deals' or 'templates'
  const [activeTab, setActiveTab] = useState('deals');

  // ─── Deals state ───
  const [deals, setDeals] = useState([]);
  const [dealsLoading, setDealsLoading] = useState(false);
  const [dealSearch, setDealSearch] = useState('');
  const [dealStatusFilter, setDealStatusFilter] = useState('');
  const [selectedDeal, setSelectedDeal] = useState(null);
  const [dealView, setDealView] = useState('list'); // list | form | detail
  const [dealFormData, setDealFormData] = useState({ ...EMPTY_DEAL });
  const [dealFormTab, setDealFormTab] = useState('application');
  const [dealSaving, setDealSaving] = useState(false);
  const [dealError, setDealError] = useState('');
  const [dealSuccess, setDealSuccess] = useState('');

  // ─── Templates state (original) ───
  const [templates, setTemplates] = useState([]);
  const [packets, setPackets] = useState([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [selectedPacket, setSelectedPacket] = useState(null);
  const [generationStatus, setGenerationStatus] = useState('idle');
  const [downloadUrl, setDownloadUrl] = useState(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [generationResult, setGenerationResult] = useState(null);
  const [fetchError, setFetchError] = useState('');

  // ─── Deal document generation state ───
  const [dealDocGenerating, setDealDocGenerating] = useState(false);
  const [dealDocResult, setDealDocResult] = useState(null);
  const [dealDocError, setDealDocError] = useState('');
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);

  // ─── Inventory picker state ───
  const [showInventoryPicker, setShowInventoryPicker] = useState(false);
  const [inventoryItems, setInventoryItems] = useState([]);
  const [inventoryLoading, setInventoryLoading] = useState(false);

  // Auto-save timer ref
  const saveTimerRef = useRef(null);

  // ─── Effects ───
  useEffect(() => {
    fetchTemplates();
  }, []);

  useEffect(() => {
    if (activeTab === 'deals' && dealView === 'list') {
      fetchDeals();
    }
  }, [activeTab, dealView, dealStatusFilter]);

  // ═══════════════════════════════════════════════════════════════════
  // DEALS API
  // ═══════════════════════════════════════════════════════════════════
  const fetchDeals = async () => {
    setDealsLoading(true);
    setDealError('');
    try {
      const params = new URLSearchParams();
      if (dealStatusFilter) params.set('status', dealStatusFilter);
      if (dealSearch) params.set('q', dealSearch);
      const res = await fetch(`/api/deals?${params}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setDeals(data.deals || []);
    } catch (e) {
      setDealError(e.message);
    } finally {
      setDealsLoading(false);
    }
  };

  const createDeal = async () => {
    setDealSaving(true);
    setDealError('');
    try {
      const res = await fetch('/api/deals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dealFormData),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setSelectedDeal(data.deal);
      setDealFormData(data.deal);
      setDealView('detail');
      setDealSuccess('Deal created successfully!');
      setTimeout(() => setDealSuccess(''), 3000);
    } catch (e) {
      setDealError(e.message);
    } finally {
      setDealSaving(false);
    }
  };

  const updateDeal = async (data = null) => {
    const dealId = selectedDeal?.id;
    if (!dealId) return;
    const payload = data || dealFormData;
    // Strip read-only fields
    const { id, created_at, ...updateData } = payload;
    try {
      const res = await fetch(`/api/deals/${dealId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updateData),
      });
      const result = await res.json();
      if (result.error) throw new Error(result.error);
      return true;
    } catch (e) {
      setDealError(e.message);
      return false;
    }
  };

  const changeDealStatus = async (newStatus) => {
    const dealId = selectedDeal?.id;
    if (!dealId) return;
    try {
      const res = await fetch(`/api/deals/${dealId}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setSelectedDeal(prev => ({ ...prev, status: newStatus }));
      setDealFormData(prev => ({ ...prev, status: newStatus }));
      setDealSuccess(`Status changed to ${STATUS_CONFIG[newStatus]?.label || newStatus}`);
      setTimeout(() => setDealSuccess(''), 3000);
    } catch (e) {
      setDealError(e.message);
    }
  };

  const archiveDeal = async () => {
    if (!window.confirm('Archive this deal? It will be hidden from the active pipeline.')) return;
    await changeDealStatus('archived');
    setDealView('list');
  };

  // ─── Auto-save on blur ───
  const scheduleAutoSave = useCallback(() => {
    if (!selectedDeal?.id) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      await updateDeal();
    }, 1500);
  }, [selectedDeal, dealFormData]);

  const handleDealFieldChange = (field, value) => {
    setDealFormData(prev => ({ ...prev, [field]: value }));
  };

  const handleDealFieldBlur = () => {
    if (selectedDeal?.id) {
      scheduleAutoSave();
    }
  };

  // ─── Generate document from deal ───
  const generateDocFromDeal = async (templateName) => {
    const dealId = selectedDeal?.id;
    if (!dealId) return;
    setDealDocGenerating(true);
    setDealDocError('');
    setDealDocResult(null);
    try {
      const res = await fetch(`/api/deals/${dealId}/generate-document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: templateName }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setDealDocResult(data);
      setShowTemplatePicker(false);
    } catch (e) {
      setDealDocError(e.message);
    } finally {
      setDealDocGenerating(false);
    }
  };

  const generatePacketFromDeal = async (packetName = 'standard_closing') => {
    const dealId = selectedDeal?.id;
    if (!dealId) return;
    setDealDocGenerating(true);
    setDealDocError('');
    setDealDocResult(null);
    try {
      const res = await fetch(`/api/deals/${dealId}/generate-packet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ packet_name: packetName }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setDealDocResult(data);
    } catch (e) {
      setDealDocError(e.message);
    } finally {
      setDealDocGenerating(false);
    }
  };

  // ─── Inventory picker ───
  const fetchInventory = async () => {
    setInventoryLoading(true);
    try {
      const res = await fetch('/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          userId: 'deal_form_user',
          sessionId: 'inventory_lookup',
          newMessage: { role: 'user', parts: [{ text: 'search all inventory' }] },
        }),
      });
      // Fallback: use the inventory search API directly if available
    } catch { /* ignore */ }
    // Simple approach: fetch from the deals endpoint which exposes inventory
    try {
      const res = await fetch('/api/documents/templates');
      // We'll use a simpler inventory search
    } catch { /* ignore */ }
    setInventoryLoading(false);
  };

  // ═══════════════════════════════════════════════════════════════════
  // TEMPLATES API (original, unchanged)
  // ═══════════════════════════════════════════════════════════════════
  const fetchTemplates = async () => {
    setFetchError('');
    try {
      const response = await fetch('/api/documents/templates');
      if (!response.ok) throw new Error(`Server returned ${response.status}`);
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      setTemplates(data.templates || []);
      setPackets(data.packets || []);
    } catch (e) {
      console.error('Failed to load templates:', e);
      setFetchError('Unable to load document templates. Please try again.');
    } finally {
      setTemplatesLoading(false);
    }
  };

  const filteredTemplates = templates.filter(t => {
    const matchesSearch = !searchQuery ||
      t.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory = activeCategory === 'all' || t.category === activeCategory;
    return matchesSearch && matchesCategory;
  });

  const groupedTemplates = {};
  filteredTemplates.forEach(t => {
    if (!groupedTemplates[t.category]) groupedTemplates[t.category] = [];
    groupedTemplates[t.category].push(t);
  });

  const handleGenerateDocument = async (formData) => {
    setGenerationStatus('generating');
    setErrorMessage('');
    setDownloadUrl(null);
    try {
      const response = await fetch('/api/documents/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: selectedTemplate.template_name, data: formData }),
      });
      const data = await response.json();
      if (data.success) {
        setGenerationStatus('success');
        setDownloadUrl(data.download_url);
        setGenerationResult(data);
      } else {
        setGenerationStatus('error');
        setErrorMessage(data.error || 'Unknown error occurred');
      }
    } catch (e) {
      setGenerationStatus('error');
      setErrorMessage(e.message);
    }
  };

  const handleGeneratePacket = async (formData) => {
    setGenerationStatus('generating');
    setErrorMessage('');
    setDownloadUrl(null);
    try {
      const response = await fetch('/api/documents/generate-packet', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ packet_name: selectedPacket.packet_name, data: formData }),
      });
      const data = await response.json();
      if (data.success) {
        setGenerationStatus('success');
        setDownloadUrl(data.download_url);
        setGenerationResult(data);
      } else {
        setGenerationStatus('error');
        setErrorMessage(data.error || 'Unknown error occurred');
      }
    } catch (e) {
      setGenerationStatus('error');
      setErrorMessage(e.message);
    }
  };

  const resetToDocList = () => {
    setSelectedTemplate(null);
    setSelectedPacket(null);
    setGenerationStatus('idle');
    setDownloadUrl(null);
    setErrorMessage('');
    setGenerationResult(null);
  };

  // ═══════════════════════════════════════════════════════════════════
  // DEAL FORM - Input helper
  // ═══════════════════════════════════════════════════════════════════
  const DealInput = ({ label, field, type = 'text', placeholder = '', required = false, options = null }) => {
    const value = dealFormData[field] ?? '';

    if (type === 'select' && options) {
      return (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
          <select
            value={value}
            onChange={(e) => handleDealFieldChange(field, e.target.value)}
            onBlur={handleDealFieldBlur}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          >
            <option value="">Select...</option>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      );
    }

    if (type === 'checkbox') {
      return (
        <div className="flex items-center mt-6">
          <input
            type="checkbox"
            checked={!!value}
            onChange={(e) => { handleDealFieldChange(field, e.target.checked); handleDealFieldBlur(); }}
            className="h-4 w-4 text-blue-600 rounded border-gray-300"
          />
          <label className="ml-2 text-sm text-gray-700">{label}</label>
        </div>
      );
    }

    if (type === 'textarea') {
      return (
        <div className="col-span-2">
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
          <textarea
            value={value}
            onChange={(e) => handleDealFieldChange(field, e.target.value)}
            onBlur={handleDealFieldBlur}
            rows={3}
            placeholder={placeholder}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
      );
    }

    if (type === 'password') {
      return (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {label} {required && <span className="text-red-500">*</span>}
            <span className="text-xs text-red-400 ml-1">(PII)</span>
          </label>
          <input
            type="password"
            value={value}
            onChange={(e) => handleDealFieldChange(field, e.target.value)}
            onBlur={handleDealFieldBlur}
            placeholder="***-**-****"
            maxLength={11}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
      );
    }

    return (
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {label} {required && <span className="text-red-500">*</span>}
        </label>
        <input
          type={type}
          value={value}
          onChange={(e) => handleDealFieldChange(field, e.target.value)}
          onBlur={handleDealFieldBlur}
          placeholder={placeholder}
          required={required}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  // RENDER: TEMPLATE FORM VIEW (original behavior, unchanged)
  // ═══════════════════════════════════════════════════════════════════
  if (activeTab === 'templates' && (selectedTemplate || selectedPacket)) {
    const templateName = selectedTemplate
      ? selectedTemplate.template_name
      : (selectedPacket.templates?.[0] || '');
    const displayName = selectedTemplate
      ? selectedTemplate.display_name
      : selectedPacket.display_name;

    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <button
          onClick={resetToDocList}
          className="flex items-center text-blue-600 hover:text-blue-800 mb-6 transition-colors"
        >
          <ArrowLeft size={20} className="mr-1" /> Back to Documents
        </button>

        {generationStatus === 'idle' && (
          <SmartForm
            templateName={templateName}
            title={displayName}
            sessionId={sessionId}
            onSubmit={selectedPacket ? handleGeneratePacket : handleGenerateDocument}
            onCancel={resetToDocList}
          />
        )}

        {generationStatus === 'generating' && (
          <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md">
            <Loader2 className="h-12 w-12 animate-spin text-blue-600 mb-4" />
            <p className="text-lg text-gray-700">Generating {displayName}...</p>
          </div>
        )}

        {generationStatus === 'success' && (
          <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md text-center">
            <CheckCircle size={64} className="text-green-500 mb-4" />
            <h2 className="text-2xl font-bold text-gray-800 mb-2">Document Ready!</h2>
            <p className="text-gray-600 mb-2">{generationResult?.message}</p>
            {generationResult?.page_count && (
              <p className="text-sm text-gray-500 mb-4">
                {generationResult.page_count} pages | {generationResult.documents_included?.length || 0} documents
              </p>
            )}
            <div className="flex space-x-4">
              <a
                href={downloadUrl}
                download
                className="flex items-center px-6 py-3 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
                target="_blank"
                rel="noreferrer"
              >
                <Download size={20} className="mr-2" /> Download PDF
              </a>
              <button
                onClick={resetToDocList}
                className="px-6 py-3 border border-gray-300 rounded-md hover:bg-gray-50 transition"
              >
                Back to Documents
              </button>
            </div>
          </div>
        )}

        {generationStatus === 'error' && (
          <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md text-center">
            <AlertCircle size={64} className="text-red-500 mb-4" />
            <h2 className="text-2xl font-bold text-gray-800 mb-2">Generation Failed</h2>
            <p className="text-gray-600 mb-6">{errorMessage}</p>
            <button
              onClick={() => setGenerationStatus('idle')}
              className="px-6 py-3 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
            >
              Try Again
            </button>
          </div>
        )}
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  // RENDER: DEAL FORM (create / edit)
  // ═══════════════════════════════════════════════════════════════════
  if (activeTab === 'deals' && dealView === 'form') {
    const isEditing = !!selectedDeal?.id;

    return (
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <button
          onClick={() => { setDealView(isEditing ? 'detail' : 'list'); setDealError(''); }}
          className="flex items-center text-blue-600 hover:text-blue-800 mb-6 transition-colors"
        >
          <ArrowLeft size={20} className="mr-1" /> {isEditing ? 'Back to Deal' : 'Back to Deals'}
        </button>

        <div className="bg-white rounded-xl shadow-md overflow-hidden">
          {/* Form Header */}
          <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-gray-900">
                {isEditing ? `Edit: ${buyerDisplayName(dealFormData)}` : 'New Deal'}
              </h2>
              <p className="text-sm text-gray-500 mt-0.5">
                {isEditing ? 'Changes save automatically' : 'Fill in buyer info and save to create the deal'}
              </p>
            </div>
            {isEditing && <StatusBadge status={dealFormData.status} />}
          </div>

          {/* Form Tabs */}
          <div className="border-b border-gray-200 px-6">
            <nav className="flex space-x-6 -mb-px">
              {[
                { key: 'application', label: 'Application', icon: Users },
                { key: 'home', label: 'Home Info', icon: Home },
                { key: 'pricing', label: 'Pricing & Loan', icon: DollarSign },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setDealFormTab(tab.key)}
                  className={`flex items-center py-3 px-1 border-b-2 text-sm font-medium transition ${
                    dealFormTab === tab.key
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                >
                  <tab.icon size={16} className="mr-1.5" />
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {/* Form Body */}
          <div className="p-6">
            {/* Success / Error banners */}
            {dealSuccess && (
              <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm flex items-center">
                <CheckCircle size={16} className="mr-2" /> {dealSuccess}
              </div>
            )}
            {dealError && (
              <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm flex items-center">
                <AlertCircle size={16} className="mr-2" /> {dealError}
                <button onClick={() => setDealError('')} className="ml-auto"><X size={14} /></button>
              </div>
            )}

            {/* ─── TAB: Application ─── */}
            {dealFormTab === 'application' && (
              <div className="space-y-6">
                {/* Salesrep */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Briefcase size={16} className="mr-2 text-gray-500" /> Assignment
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Sales Rep" field="salesrep" placeholder="e.g. John Doe" />
                  </div>
                </div>

                {/* Buyer */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Users size={16} className="mr-2 text-gray-500" /> Buyer
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="First Name" field="buyer_first_name" required />
                    <DealInput label="Last Name" field="buyer_last_name" required />
                    <DealInput label="Phone" field="buyer_phone" type="tel" />
                    <DealInput label="Email" field="buyer_email" type="email" />
                    <DealInput label="SSN" field="buyer_ssn" type="password" />
                    <DealInput label="Marital Status" field="buyer_marital_status" type="select"
                      options={['Single', 'Married', 'Divorced', 'Widowed']} />
                  </div>
                </div>

                {/* Co-Buyer */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Users size={16} className="mr-2 text-gray-500" /> Co-Buyer
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="First Name" field="co_buyer_first_name" />
                    <DealInput label="Last Name" field="co_buyer_last_name" />
                    <DealInput label="Phone" field="co_buyer_phone" type="tel" />
                    <DealInput label="SSN" field="co_buyer_ssn" type="password" />
                    <DealInput label="Marital Status" field="co_buyer_marital_status" type="select"
                      options={['Single', 'Married', 'Divorced', 'Widowed']} />
                  </div>
                </div>

                {/* Employment - Buyer */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Briefcase size={16} className="mr-2 text-gray-500" /> Employment (Buyer)
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Employer" field="employer_name" />
                    <DealInput label="Occupation" field="occupation" />
                    <DealInput label="Years at Job" field="occupation_length" />
                    <DealInput label="Work Phone" field="work_phone" type="tel" />
                    <DealInput label="Self Employed" field="self_employed" type="checkbox" />
                    <DealInput label="Previous Employer" field="previous_employer" />
                    <DealInput label="Previous Occupation" field="previous_occupation" />
                  </div>
                </div>

                {/* Employment - Co-Buyer */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Briefcase size={16} className="mr-2 text-gray-500" /> Employment (Co-Buyer)
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Employer" field="co_buyer_employer" />
                    <DealInput label="Occupation" field="co_buyer_occupation" />
                    <DealInput label="Years at Job" field="co_buyer_occupation_length" />
                    <DealInput label="Work Phone" field="co_buyer_work_phone" type="tel" />
                    <DealInput label="Self Employed" field="co_buyer_self_employed" type="checkbox" />
                  </div>
                </div>

                {/* Mailing Address */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <MapPin size={16} className="mr-2 text-gray-500" /> Mailing Address (Current Residence)
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="col-span-2">
                      <DealInput label="Street Address" field="mailing_address" />
                    </div>
                    <DealInput label="City" field="mailing_city" />
                    <DealInput label="State" field="mailing_state" />
                    <DealInput label="Zip" field="mailing_zip" />
                    <DealInput label="Years at Address" field="mailing_length" />
                    <DealInput label="Own or Rent" field="mailing_own_rent" type="select"
                      options={['Own', 'Rent']} />
                  </div>
                </div>

                {/* References */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Phone size={16} className="mr-2 text-gray-500" /> References
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Reference 1 Name" field="reference1_name" />
                    <DealInput label="Reference 1 Phone" field="reference1_phone" type="tel" />
                    <DealInput label="Reference 2 Name" field="reference2_name" />
                    <DealInput label="Reference 2 Phone" field="reference2_phone" type="tel" />
                  </div>
                </div>

                {/* Notes */}
                <div>
                  <DealInput label="Notes" field="notes" type="textarea" placeholder="Additional notes..." />
                </div>
              </div>
            )}

            {/* ─── TAB: Home Info ─── */}
            {dealFormTab === 'home' && (
              <div className="space-y-6">
                {/* Home Details */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <Home size={16} className="mr-2 text-gray-500" /> Home Details
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Manufacturer" field="manufacturer" placeholder="e.g. Champion, TRU" />
                    <DealInput label="Model" field="model" />
                    <DealInput label="Year" field="year" />
                    <DealInput label="# of Sections" field="no_of_sections" type="select"
                      options={['Single', 'Double', 'Triple']} />
                    <DealInput label="Serial # 1" field="serial_number_1" />
                    <DealInput label="Serial # 2" field="serial_number_2" />
                    <DealInput label="Label # 1" field="label_number_1" />
                    <DealInput label="Label # 2" field="label_number_2" />
                    <DealInput label="New Home" field="is_new" type="checkbox" />
                  </div>
                </div>

                {/* Installation Address */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <MapPin size={16} className="mr-2 text-gray-500" /> Installation Address
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="col-span-2">
                      <DealInput label="Street Address" field="buyer_address" />
                    </div>
                    <DealInput label="City" field="buyer_city" />
                    <DealInput label="County" field="buyer_county" />
                    <DealInput label="State" field="buyer_state" />
                    <DealInput label="Zip" field="buyer_zip" />
                  </div>
                </div>
              </div>
            )}

            {/* ─── TAB: Pricing & Loan ─── */}
            {dealFormTab === 'pricing' && (
              <div className="space-y-6">
                {/* Pricing */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <DollarSign size={16} className="mr-2 text-gray-500" /> Pricing
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Sales Price" field="sales_price" type="number" placeholder="0.00" />
                    <DealInput label="Down Payment" field="down_payment" type="number" placeholder="0.00" />
                  </div>
                </div>

                {/* Lender / Creditor */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <DollarSign size={16} className="mr-2 text-gray-500" /> Creditor / Lender
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="Creditor Name" field="creditor_name" placeholder="e.g. 21st Mortgage" />
                    <DealInput label="Phone" field="creditor_phone" type="tel" />
                    <div className="col-span-2">
                      <DealInput label="Address" field="creditor_address" />
                    </div>
                    <DealInput label="City, State, Zip" field="creditor_city_state_zip" />
                  </div>
                </div>

                {/* Loan Terms */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center">
                    <DollarSign size={16} className="mr-2 text-gray-500" /> Loan Terms
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <DealInput label="APR (%)" field="apr" placeholder="e.g. 9.99" />
                    <DealInput label="Loan Term" field="loan_term" placeholder="e.g. 20 years" />
                    <DealInput label="Finance Charge" field="finance_charge" type="number" placeholder="0.00" />
                    <DealInput label="Max Financed" field="max_financed" type="number" placeholder="0.00" />
                    <DealInput label="Total of Payments" field="total_payments" type="number" placeholder="0.00" />
                    <DealInput label="Payment Start Date" field="payment_start_date" type="date" />
                    <DealInput label="Insurance Premium" field="insurance_premium" type="number" placeholder="0.00" />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Form Footer */}
          <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-between bg-gray-50">
            <button
              onClick={() => { setDealView(isEditing ? 'detail' : 'list'); setDealError(''); }}
              className="px-4 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              Cancel
            </button>
            {!isEditing && (
              <button
                onClick={createDeal}
                disabled={dealSaving || !dealFormData.buyer_first_name}
                className="px-6 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center"
              >
                {dealSaving ? <><Loader2 size={16} className="animate-spin mr-2" /> Saving...</> : 'Create Deal'}
              </button>
            )}
            {isEditing && (
              <button
                onClick={async () => {
                  setDealSaving(true);
                  await updateDeal();
                  setDealSaving(false);
                  setDealSuccess('Changes saved!');
                  setTimeout(() => setDealSuccess(''), 3000);
                }}
                disabled={dealSaving}
                className="px-6 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center"
              >
                {dealSaving ? <><Loader2 size={16} className="animate-spin mr-2" /> Saving...</> : 'Save Changes'}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  // RENDER: DEAL DETAIL VIEW
  // ═══════════════════════════════════════════════════════════════════
  if (activeTab === 'deals' && dealView === 'detail' && selectedDeal) {
    const deal = dealFormData;
    const currentStatusIdx = STATUS_FLOW.indexOf(deal.status);
    const nextStatus = currentStatusIdx >= 0 && currentStatusIdx < STATUS_FLOW.length - 1
      ? STATUS_FLOW[currentStatusIdx + 1]
      : null;

    return (
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <button
          onClick={() => { setDealView('list'); setSelectedDeal(null); setDealDocResult(null); setDealDocError(''); }}
          className="flex items-center text-blue-600 hover:text-blue-800 mb-6 transition-colors"
        >
          <ArrowLeft size={20} className="mr-1" /> Back to Deals
        </button>

        {/* Deal Header */}
        <div className="bg-white rounded-xl shadow-md p-6 mb-6">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-2xl font-bold text-gray-900">{buyerDisplayName(deal)}</h2>
              <div className="flex items-center gap-3 mt-2 text-sm text-gray-500">
                {deal.buyer_phone && <span className="flex items-center"><Phone size={14} className="mr-1" /> {deal.buyer_phone}</span>}
                {deal.buyer_email && <span className="flex items-center"><Mail size={14} className="mr-1" /> {deal.buyer_email}</span>}
                {deal.salesrep && <span className="flex items-center"><Briefcase size={14} className="mr-1" /> {deal.salesrep}</span>}
              </div>
              {deal.manufacturer && deal.model && (
                <p className="mt-1 text-sm text-gray-600">
                  <Home size={14} className="inline mr-1" />
                  {deal.year} {deal.manufacturer} {deal.model}
                </p>
              )}
              <p className="mt-1 text-xs text-gray-400">Created: {formatDate(deal.created_at || selectedDeal.created_at)}</p>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge status={deal.status} />
              {deal.status !== 'archived' && deal.status !== 'denied' && (
                <select
                  value={deal.status}
                  onChange={(e) => changeDealStatus(e.target.value)}
                  className="text-sm border border-gray-300 rounded-lg px-2 py-1"
                >
                  {Object.entries(STATUS_CONFIG).map(([key, cfg]) => (
                    <option key={key} value={key}>{cfg.label}</option>
                  ))}
                </select>
              )}
            </div>
          </div>

          {/* Status Progress Bar */}
          {deal.status !== 'archived' && deal.status !== 'denied' && (
            <div className="mt-4 flex items-center gap-1">
              {STATUS_FLOW.map((s, i) => (
                <div key={s} className="flex items-center flex-1">
                  <div className={`h-2 flex-1 rounded-full ${
                    i <= currentStatusIdx ? 'bg-blue-600' : 'bg-gray-200'
                  }`} />
                </div>
              ))}
            </div>
          )}

          {/* Success / Error */}
          {dealSuccess && (
            <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm flex items-center">
              <CheckCircle size={16} className="mr-2" /> {dealSuccess}
            </div>
          )}
          {dealError && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
              <AlertCircle size={16} className="inline mr-2" /> {dealError}
            </div>
          )}

          {/* Action Buttons */}
          <div className="mt-4 flex flex-wrap gap-2">
            {nextStatus && (
              <button
                onClick={() => changeDealStatus(nextStatus)}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center"
              >
                <ChevronRight size={16} className="mr-1" />
                Move to {STATUS_CONFIG[nextStatus]?.label}
              </button>
            )}
            <button
              onClick={() => { setDealView('form'); setDealFormTab('application'); }}
              className="px-4 py-2 text-sm bg-white border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 flex items-center"
            >
              <Edit3 size={16} className="mr-1" /> Edit Deal
            </button>
            <button
              onClick={archiveDeal}
              className="px-4 py-2 text-sm bg-white border border-gray-300 text-gray-500 rounded-lg hover:bg-gray-50 flex items-center"
            >
              <Archive size={16} className="mr-1" /> Archive
            </button>
          </div>
        </div>

        {/* Deal Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div className="bg-white rounded-xl shadow-md p-4">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Sales Price</h4>
            <p className="text-2xl font-bold text-gray-900">
              {deal.sales_price ? `$${Number(deal.sales_price).toLocaleString()}` : '--'}
            </p>
          </div>
          <div className="bg-white rounded-xl shadow-md p-4">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Down Payment</h4>
            <p className="text-2xl font-bold text-gray-900">
              {deal.down_payment ? `$${Number(deal.down_payment).toLocaleString()}` : '--'}
            </p>
          </div>
          <div className="bg-white rounded-xl shadow-md p-4">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Financed</h4>
            <p className="text-2xl font-bold text-gray-900">
              {deal.sales_price && deal.down_payment
                ? `$${(Number(deal.sales_price) - Number(deal.down_payment)).toLocaleString()}`
                : '--'}
            </p>
          </div>
        </div>

        {/* Documents Section */}
        <div className="bg-white rounded-xl shadow-md p-6">
          <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
            <FileText size={20} className="mr-2 text-blue-600" /> Generate Documents
          </h3>

          {/* Document generation result */}
          {dealDocResult && (
            <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-lg">
              <div className="flex items-center justify-between">
                <div className="flex items-center">
                  <CheckCircle size={20} className="text-green-600 mr-2" />
                  <div>
                    <p className="text-sm font-medium text-green-800">Document Ready!</p>
                    <p className="text-xs text-green-600">{dealDocResult.message}</p>
                  </div>
                </div>
                <a
                  href={dealDocResult.download_url}
                  download
                  target="_blank"
                  rel="noreferrer"
                  className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 flex items-center"
                >
                  <Download size={16} className="mr-1" /> Download PDF
                </a>
              </div>
            </div>
          )}

          {dealDocError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
              <AlertCircle size={16} className="inline mr-2" /> {dealDocError}
            </div>
          )}

          {dealDocGenerating && (
            <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-lg flex items-center">
              <Loader2 size={20} className="animate-spin text-blue-600 mr-3" />
              <p className="text-sm text-blue-700">Generating document...</p>
            </div>
          )}

          <div className="flex flex-wrap gap-3">
            {/* Generate Closing Packet — smart ordering: recommended packet first */}
            {packets
              .sort((a, b) => {
                // Recommend the right packet based on new/used status
                const isUsed = selectedDeal && selectedDeal.is_new === false;
                const aRecommended = isUsed
                  ? a.packet_name.includes('used')
                  : (a.packet_name === 'standard_closing' || a.packet_name === 'full_closing_new');
                const bRecommended = isUsed
                  ? b.packet_name.includes('used')
                  : (b.packet_name === 'standard_closing' || b.packet_name === 'full_closing_new');
                if (aRecommended && !bRecommended) return -1;
                if (!aRecommended && bRecommended) return 1;
                return 0;
              })
              .map((packet, idx) => {
                const isUsed = selectedDeal && selectedDeal.is_new === false;
                const isRecommended = isUsed
                  ? packet.packet_name.includes('used')
                  : (packet.packet_name === 'standard_closing' || packet.packet_name === 'full_closing_new');
                return (
                  <button
                    key={packet.packet_name}
                    onClick={() => generatePacketFromDeal(packet.packet_name)}
                    disabled={dealDocGenerating}
                    className={`px-4 py-2.5 rounded-lg text-sm disabled:opacity-50 flex items-center shadow ${
                      isRecommended
                        ? 'bg-gradient-to-r from-blue-600 to-blue-700 text-white hover:from-blue-700 hover:to-blue-800 ring-2 ring-blue-300'
                        : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    <Package size={16} className="mr-2" />
                    {packet.display_name} ({packet.template_count} docs)
                    {isRecommended && <span className="ml-2 text-xs bg-blue-500 text-white px-1.5 py-0.5 rounded">Recommended</span>}
                  </button>
                );
              })}

            {/* Generate Single Document */}
            <div className="relative">
              <button
                onClick={() => setShowTemplatePicker(!showTemplatePicker)}
                disabled={dealDocGenerating}
                className="px-4 py-2.5 bg-white border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50 flex items-center"
              >
                <FileText size={16} className="mr-2" />
                Generate Single Document
              </button>

              {showTemplatePicker && (
                <div className="absolute top-full left-0 mt-2 w-80 bg-white rounded-xl shadow-xl border border-gray-200 z-50 max-h-80 overflow-y-auto">
                  <div className="p-3 border-b border-gray-100 sticky top-0 bg-white">
                    <p className="text-xs font-semibold text-gray-500 uppercase">Select Template</p>
                  </div>
                  {templates.map(t => (
                    <button
                      key={t.template_name}
                      onClick={() => generateDocFromDeal(t.template_name)}
                      className="w-full text-left px-4 py-2.5 hover:bg-blue-50 border-b border-gray-50 transition"
                    >
                      <p className="text-sm font-medium text-gray-800">{t.display_name}</p>
                      <p className="text-xs text-gray-500">{t.description}</p>
                    </button>
                  ))}
                  {templates.length === 0 && (
                    <p className="px-4 py-3 text-sm text-gray-400">No templates available</p>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  // RENDER: MAIN VIEW with Tab Switcher
  // ═══════════════════════════════════════════════════════════════════
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Document Center</h1>
          <p className="mt-1 text-gray-500">Manage deals, generate contracts, and closing packets.</p>
        </div>
        <button onClick={onBack} className="text-gray-500 hover:text-gray-700">Close</button>
      </div>

      {/* Tab Switcher */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex space-x-8">
          <button
            onClick={() => setActiveTab('deals')}
            className={`pb-3 px-1 border-b-2 text-sm font-medium transition ${
              activeTab === 'deals'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <ClipboardList size={16} className="inline mr-1.5 -mt-0.5" />
            Deals
          </button>
          <button
            onClick={() => setActiveTab('templates')}
            className={`pb-3 px-1 border-b-2 text-sm font-medium transition ${
              activeTab === 'templates'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <FileText size={16} className="inline mr-1.5 -mt-0.5" />
            Templates
          </button>
        </nav>
      </div>

      {/* ═══ DEALS TAB ═══ */}
      {activeTab === 'deals' && (
        <div>
          {/* Deals toolbar */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-6">
            <div className="relative flex-1">
              <Search size={18} className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search by buyer name..."
                value={dealSearch}
                onChange={(e) => setDealSearch(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && fetchDeals()}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              />
            </div>
            <select
              value={dealStatusFilter}
              onChange={(e) => setDealStatusFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              <option value="">All Statuses</option>
              {Object.entries(STATUS_CONFIG).map(([key, cfg]) => (
                <option key={key} value={key}>{cfg.label}</option>
              ))}
            </select>
            <button
              onClick={() => fetchDeals()}
              className="px-3 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              <Search size={16} />
            </button>
            <button
              onClick={() => {
                setSelectedDeal(null);
                setDealFormData({ ...EMPTY_DEAL });
                setDealFormTab('application');
                setDealView('form');
                setDealError('');
                setDealSuccess('');
              }}
              className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 flex items-center whitespace-nowrap"
            >
              <Plus size={16} className="mr-1" /> New Deal
            </button>
          </div>

          {/* Deals Loading */}
          {dealsLoading && (
            <div className="flex justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          )}

          {/* Deals Error */}
          {dealError && !dealsLoading && (
            <div className="text-center py-8">
              <AlertCircle size={48} className="mx-auto mb-4 text-red-400" />
              <p className="text-red-600 mb-4">{dealError}</p>
              <button onClick={fetchDeals} className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700">
                Try Again
              </button>
            </div>
          )}

          {/* Deals List */}
          {!dealsLoading && !dealError && (
            <div className="space-y-3">
              {deals.length === 0 && (
                <div className="text-center py-12 text-gray-500">
                  <ClipboardList size={48} className="mx-auto mb-4 opacity-30" />
                  <p className="text-lg font-medium mb-1">No deals yet</p>
                  <p className="text-sm">Click "New Deal" to create your first application.</p>
                </div>
              )}

              {deals.map(deal => (
                <div
                  key={deal.id}
                  onClick={() => {
                    setSelectedDeal(deal);
                    setDealFormData({ ...EMPTY_DEAL, ...deal });
                    setDealView('detail');
                    setDealDocResult(null);
                    setDealDocError('');
                  }}
                  className="bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md hover:border-blue-300 transition cursor-pointer p-4 flex items-center justify-between group"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-1">
                      <h3 className="text-base font-semibold text-gray-900 truncate group-hover:text-blue-600">
                        {buyerDisplayName(deal)}
                      </h3>
                      <StatusBadge status={deal.status} />
                    </div>
                    <div className="flex items-center gap-4 text-xs text-gray-500">
                      {deal.manufacturer && deal.model && (
                        <span><Home size={12} className="inline mr-1" />{deal.manufacturer} {deal.model}</span>
                      )}
                      {deal.salesrep && (
                        <span><Briefcase size={12} className="inline mr-1" />{deal.salesrep}</span>
                      )}
                      {deal.sales_price && (
                        <span><DollarSign size={12} className="inline mr-1" />${Number(deal.sales_price).toLocaleString()}</span>
                      )}
                      <span>{formatDate(deal.updated_at || deal.created_at)}</span>
                    </div>
                  </div>
                  <ChevronRight size={20} className="text-gray-400 group-hover:text-blue-600 flex-shrink-0 ml-2" />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ═══ TEMPLATES TAB (original behavior, unchanged) ═══ */}
      {activeTab === 'templates' && (
        <div>
          {/* Search + Category Filter */}
          <div className="mb-6 space-y-4">
            <div className="relative">
              <Search size={18} className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search documents..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setActiveCategory('all')}
                className={`px-3 py-1.5 rounded-full text-sm font-medium transition ${
                  activeCategory === 'all' ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                All ({templates.length})
              </button>
              {CATEGORY_ORDER.map(cat => {
                const count = templates.filter(t => t.category === cat).length;
                if (count === 0) return null;
                return (
                  <button
                    key={cat}
                    onClick={() => setActiveCategory(cat)}
                    className={`px-3 py-1.5 rounded-full text-sm font-medium transition ${
                      activeCategory === cat ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    }`}
                  >
                    {cat} ({count})
                  </button>
                );
              })}
            </div>
          </div>

          {/* Loading */}
          {templatesLoading && (
            <div className="flex justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          )}

          {/* Closing Packets */}
          {!templatesLoading && packets.length > 0 && activeCategory === 'all' && (
            <div className="mb-8">
              <h2 className="text-lg font-semibold text-gray-800 mb-3 flex items-center">
                <Package size={20} className="mr-2 text-blue-600" /> Closing Packets
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {packets.map(packet => (
                  <div
                    key={packet.packet_name}
                    onClick={() => setSelectedPacket(packet)}
                    className="bg-gradient-to-r from-blue-50 to-white p-5 rounded-lg shadow-md hover:shadow-lg transition cursor-pointer border-2 border-blue-200 hover:border-blue-500"
                  >
                    <div className="flex items-center mb-2">
                      <Package size={20} className="text-blue-600 mr-2" />
                      <h3 className="text-lg font-medium text-gray-900">{packet.display_name}</h3>
                    </div>
                    <p className="text-sm text-gray-500 mb-2">{packet.description}</p>
                    <p className="text-xs text-blue-600 font-medium">{packet.template_count} documents included</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Templates by Category */}
          {!templatesLoading && CATEGORY_ORDER.map(category => {
            const categoryTemplates = groupedTemplates[category];
            if (!categoryTemplates || categoryTemplates.length === 0) return null;

            return (
              <div key={category} className="mb-8">
                <h2 className="text-lg font-semibold text-gray-800 mb-3">{category} Forms</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {categoryTemplates.map(template => (
                    <div
                      key={template.template_name}
                      onClick={() => setSelectedTemplate(template)}
                      className="bg-white p-5 rounded-lg shadow-md hover:shadow-lg transition cursor-pointer border border-transparent hover:border-blue-500 group"
                    >
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center justify-center h-10 w-10 rounded-md bg-blue-100 text-blue-600 group-hover:bg-blue-600 group-hover:text-white transition-colors">
                          <FileText size={20} />
                        </div>
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[template.category] || 'bg-gray-100 text-gray-600'}`}>
                          {template.category}
                        </span>
                      </div>
                      <h3 className="text-base font-medium text-gray-900 mb-1">{template.display_name}</h3>
                      <p className="text-sm text-gray-500 mb-2 line-clamp-2">{template.description}</p>
                      <p className="text-xs text-gray-400">{template.field_count} fields</p>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}

          {!templatesLoading && fetchError && (
            <div className="text-center py-12">
              <AlertCircle size={48} className="mx-auto mb-4 text-red-400" />
              <p className="text-red-600 mb-4">{fetchError}</p>
              <button
                onClick={fetchTemplates}
                className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
              >
                Try Again
              </button>
            </div>
          )}

          {!templatesLoading && !fetchError && filteredTemplates.length === 0 && (
            <div className="text-center py-12 text-gray-500">
              <FileText size={48} className="mx-auto mb-4 opacity-30" />
              <p>No documents match your search.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default DocumentCenter;
