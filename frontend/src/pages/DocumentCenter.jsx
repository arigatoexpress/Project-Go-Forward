import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText, Download, CheckCircle, AlertCircle, Search,
  Loader2, ChevronRight, ChevronDown, Home, Plus, X,
  Package, User, DollarSign, Briefcase, MapPin,
  ArrowRight, ArrowLeft, RotateCcw, Check, FileCheck,
  Building2, Phone, Mail, Calendar, Hash, MapPinned,
  CreditCard, BadgeDollarSign, ClipboardList, FolderOpen,
  Info, Eye, HelpCircle
} from 'lucide-react';
import adminFetch from '../adminFetch';

/* ─── Constants ──────────────────────────────────────────── */

const STEPS = [
  { num: 1, label: 'Customer Info', desc: 'Enter buyer details' },
  { num: 2, label: 'Choose Home', desc: 'Select from inventory' },
  { num: 3, label: 'Pick Documents', desc: 'Select what to generate' },
  { num: 4, label: 'Review & Generate', desc: 'Download PDFs' }
];

const INITIAL_FORM = {
  // Customer Info
  salesrep: '',
  buyer_first_name: '', buyer_last_name: '', buyer_phone: '', buyer_email: '',
  buyer_ssn: '', buyer_dob: '', buyer_marital_status: '',
  co_buyer_first_name: '', co_buyer_last_name: '', co_buyer_phone: '',
  co_buyer_ssn: '', co_buyer_dob: '', co_buyer_marital_status: '',
  mailing_address: '', mailing_city: '', mailing_state: 'TX', mailing_zip: '',
  employer_name: '', occupation: '', occupation_length: '', work_phone: '',
  // Home Info
  is_new: true, manufacturer: '', model: '', year: '',
  serial_number_1: '', serial_number_2: '',
  label_number_1: '', label_number_2: '', no_of_sections: '',
  buyer_address: '', buyer_city: '', buyer_county: '', buyer_state: 'TX', buyer_zip: '',
  // Financial
  sales_price: '', down_payment: '',
  creditor_name: '', creditor_phone: '', creditor_address: '', creditor_city_state_zip: '',
  loan_term: '', apr: '', finance_charge: '', max_financed: '',
  monthly_payment: '', total_payments: '', payment_start_date: '', insurance_premium: '',
};

const CAT_COLORS = {
  TMHA: 'bg-blue-100 text-blue-800 border-blue-300',
  TDHCA: 'bg-green-100 text-green-800 border-green-300',
  State: 'bg-amber-100 text-amber-800 border-amber-300',
  Internal: 'bg-purple-100 text-purple-800 border-purple-300',
};
const CAT_ORDER = ['TMHA', 'TDHCA', 'State', 'Internal'];

const CAT_ICONS = {
  TMHA: Building2,
  TDHCA: FileCheck,
  State: ClipboardList,
  Internal: FolderOpen,
};

function toDocumentData(f) {
  const buyer = `${f.buyer_first_name} ${f.buyer_last_name}`.trim();
  const coBuyer = `${f.co_buyer_first_name} ${f.co_buyer_last_name}`.trim();
  return {
    ...f,
    buyer_name: buyer || undefined,
    co_buyer_name: coBuyer || undefined,
    buyer_city_state_zip: f.buyer_city ? `${f.buyer_city}, ${f.buyer_state || 'TX'} ${f.buyer_zip}`.trim() : undefined
  };
}

/* ─── Reusable Components ────────────────────────────────── */

function StepBar({ step }) {
  return (
    <div className="mb-8">
      <div className="flex items-center justify-between">
        {STEPS.map((s, i) => {
          const isActive = step === s.num;
          const isDone = step > s.num;
          const isLast = i === STEPS.length - 1;

          return (
            <React.Fragment key={s.num}>
              <div className="flex flex-col items-center flex-1">
                <div className={`
                  w-12 h-12 rounded-full flex items-center justify-center text-lg font-bold
                  transition-all duration-300 border-3
                  ${isDone ? 'bg-green-500 text-white border-green-500' :
                    isActive ? 'bg-blue-600 text-white border-blue-600 ring-4 ring-blue-200' :
                    'bg-white text-gray-400 border-gray-300'}
                `}>
                  {isDone ? <Check size={24} /> : s.num}
                </div>
                <div className="mt-2 text-center hidden sm:block">
                  <div className={`text-sm font-bold ${isActive ? 'text-blue-700' : isDone ? 'text-green-600' : 'text-gray-400'}`}>
                    {s.label}
                  </div>
                  <div className="text-xs text-gray-500">{s.desc}</div>
                </div>
              </div>
              {!isLast && (
                <div className={`w-full h-1 mx-2 ${isDone ? 'bg-green-400' : 'bg-gray-200'}`} />
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

function Section({ title, icon: Icon, children, open: initOpen = true, badge, helpText }) {
  const [open, setOpen] = useState(initOpen);
  return (
    <div className="bg-white rounded-xl border-2 border-gray-200 overflow-hidden shadow-sm">
      <button type="button" onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-6 py-4 bg-gray-50 hover:bg-gray-100 transition-colors">
        <div className="flex items-center gap-3">
          {Icon && <Icon size={22} className="text-blue-600" />}
          <span className="font-bold text-gray-800 text-base">{title}</span>
          {badge}
          {helpText && (
            <div className="relative group">
              <HelpCircle size={16} className="text-gray-400 cursor-help" />
              <div className="absolute left-full ml-2 top-1/2 -translate-y-1/2 w-64 p-2 bg-gray-800 text-white text-xs rounded-lg opacity-0 group-hover:opacity-100 transition-opacity z-10 pointer-events-none">
                {helpText}
              </div>
            </div>
          )}
        </div>
        {open ? <ChevronDown size={22} className="text-gray-500" /> : <ChevronRight size={22} className="text-gray-500" />}
      </button>
      {open && <div className="px-6 pb-6 pt-4 border-t border-gray-200">{children}</div>}
    </div>
  );
}

function Field({ label, name, value, onChange, type = 'text', placeholder, half, third, required, readOnly, icon: Icon }) {
  const inputClasses = `
    w-full px-4 py-3 border-2 rounded-xl text-base transition-all
    focus:ring-4 focus:ring-blue-200 focus:border-blue-500 outline-none
    ${readOnly ? 'bg-gray-100 text-gray-500 border-gray-200' : 'bg-white border-gray-300 hover:border-gray-400'}
  `;

  const widthClass = third ? 'w-full sm:w-1/3' : half ? 'w-full sm:w-1/2' : 'w-full';

  return (
    <div className={`${widthClass} px-2 mb-4`}>
      <label className="block text-sm font-bold text-gray-700 mb-2">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </label>
      <div className="relative">
        {Icon && <Icon size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />}
        <input
          type={type === 'ssn' ? 'password' : type === 'currency' ? 'text' : type}
          inputMode={type === 'currency' ? 'decimal' : type === 'phone' ? 'tel' : undefined}
          name={name}
          value={value || ''}
          onChange={e => onChange(name, e.target.value)}
          placeholder={placeholder}
          readOnly={readOnly}
          className={`${inputClasses} ${Icon ? 'pl-10' : ''}`}
        />
      </div>
    </div>
  );
}

function Row({ children }) {
  return <div className="flex flex-wrap -mx-2">{children}</div>;
}

function BigButton({ children, onClick, disabled, variant = 'primary', icon: Icon, className = '' }) {
  const variants = {
    primary: 'bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300 shadow-lg shadow-blue-200',
    secondary: 'bg-white text-gray-700 border-2 border-gray-300 hover:bg-gray-50 disabled:bg-gray-100',
    success: 'bg-green-600 text-white hover:bg-green-700 disabled:bg-green-300 shadow-lg shadow-green-200',
    danger: 'bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300',
  };

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        flex items-center justify-center gap-2 px-6 py-4 rounded-xl font-bold text-lg
        transition-all transform hover:scale-[1.02] active:scale-[0.98]
        disabled:cursor-not-allowed disabled:transform-none
        ${variants[variant]} ${className}
      `}
    >
      {Icon && <Icon size={22} />}
      {children}
    </button>
  );
}

function Card({ children, className = '' }) {
  return (
    <div className={`bg-white rounded-xl border-2 border-gray-200 shadow-sm ${className}`}>
      {children}
    </div>
  );
}

function Badge({ children, color = 'blue' }) {
  const colors = {
    blue: 'bg-blue-100 text-blue-800',
    green: 'bg-green-100 text-green-800',
    amber: 'bg-amber-100 text-amber-800',
    purple: 'bg-purple-100 text-purple-800',
    gray: 'bg-gray-100 text-gray-700',
  };
  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-bold ${colors[color]}`}>
      {children}
    </span>
  );
}

/* ─── Duplicate Warning Component ────────────────────────── */

function DuplicateWarning({ warning, onViewDeal }) {
  if (!warning) return null;
  
  return (
    <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-4 mb-6">
      <div className="flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <h4 className="font-bold text-amber-800">Potential Duplicate Deal Found</h4>
          <p className="text-sm text-amber-700 mt-1">
            {warning.count} existing deal{warning.count > 1 ? 's' : ''} found with similar information.
          </p>
          <div className="mt-3 space-y-2">
            {warning.deals.map((deal, i) => (
              <button
                key={deal.id || i}
                onClick={() => onViewDeal(deal)}
                className="w-full text-left p-3 bg-white rounded-lg border border-amber-200 hover:border-amber-400 transition-colors"
              >
                <div className="font-medium text-gray-800">
                  {deal.buyer_first_name} {deal.buyer_last_name}
                </div>
                {deal.model && <div className="text-sm text-gray-500">{deal.model}</div>}
                <div className="text-xs text-gray-400 mt-1">
                  Created: {new Date(deal.created_at).toLocaleDateString()}
                </div>
              </button>
            ))}
          </div>
          <p className="text-xs text-amber-600 mt-3">
            Click a deal above to load it, or continue with new information.
          </p>
        </div>
      </div>
    </div>
  );
}

/* ─── Validation Errors Component ────────────────────────── */

function ValidationErrors({ errors }) {
  if (!errors || errors.length === 0) return null;
  
  return (
    <div className="bg-red-50 border-2 border-red-200 rounded-xl p-4 mb-6">
      <div className="flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
        <div>
          <h4 className="font-bold text-red-800">Please fix the following:</h4>
          <ul className="mt-2 space-y-1">
            {errors.map((error, i) => (
              <li key={i} className="text-sm text-red-700 flex items-center gap-2">
                <span className="w-1.5 h-1.5 bg-red-400 rounded-full" />
                {error}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/* ─── Step 1: Customer Info ──────────────────────────────── */

function Step1({ data, onChange, deals, dealsLoading, onLoadDeal, onNext, validationErrors, duplicateWarning, onViewDuplicate }) {
  const [q, setQ] = useState('');
  const [showPicker, setShowPicker] = useState(false);

  const filtered = (deals || []).filter(d => {
    if (!q) return true;
    const s = q.toLowerCase();
    return `${d.buyer_first_name || ''} ${d.buyer_last_name || ''}`.toLowerCase().includes(s)
      || (d.model || '').toLowerCase().includes(s)
      || (d.id || '').toLowerCase().includes(s);
  }).slice(0, 6);

  const c = (n, v) => onChange(n, v);

  const canProceed = data.buyer_first_name && data.buyer_last_name;

  return (
    <div className="space-y-6">
      {/* Validation Errors */}
      <ValidationErrors errors={validationErrors} />
      
      {/* Duplicate Warning */}
      <DuplicateWarning warning={duplicateWarning} onViewDeal={onViewDuplicate} />
      
      {/* Quick Load from Deal */}
      <Card className="p-6 bg-gradient-to-r from-blue-50 to-blue-100 border-blue-200">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 bg-blue-600 rounded-xl flex items-center justify-center">
              <Search size={24} className="text-white" />
            </div>
            <div>
              <h3 className="font-bold text-gray-800 text-lg">Load from Existing Deal</h3>
              <p className="text-sm text-gray-600">Quickly fill forms with saved deal information</p>
            </div>
          </div>
          <BigButton
            variant="secondary"
            onClick={() => setShowPicker(!showPicker)}
            icon={showPicker ? ChevronDown : ChevronRight}
          >
            {showPicker ? 'Hide Deals' : 'Find Deal'}
          </BigButton>
        </div>

        {showPicker && (
          <div className="mt-4 pt-4 border-t border-blue-200">
            <div className="relative mb-4">
              <Search size={20} className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                value={q}
                onChange={e => setQ(e.target.value)}
                placeholder="Search by buyer name, home model, or deal ID..."
                className="w-full pl-12 pr-4 py-4 border-2 border-blue-300 rounded-xl text-lg bg-white focus:ring-4 focus:ring-blue-200 outline-none"
              />
            </div>

            {dealsLoading ? (
              <div className="flex items-center justify-center gap-3 py-8 text-blue-600">
                <Loader2 size={28} className="animate-spin" />
                <span className="text-lg font-medium">Loading deals...</span>
              </div>
            ) : filtered.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {filtered.map(d => (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => { onLoadDeal(d); setShowPicker(false); setQ(''); }}
                    className="text-left p-4 rounded-xl bg-white border-2 border-blue-200 hover:border-blue-400 hover:shadow-md transition-all"
                  >
                    <div className="font-bold text-gray-800 text-base">
                      {d.buyer_first_name} {d.buyer_last_name}
                    </div>
                    {d.model && <div className="text-gray-600 mt-1">{d.model}</div>}
                    <div className="flex items-center gap-2 mt-2">
                      <Badge color={d.is_new ? 'green' : 'amber'}>
                        {d.is_new ? 'New Home' : 'Pre-Owned'}
                      </Badge>
                      <span className="text-sm text-gray-400">#{d.id?.slice(-6)}</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500">
                <Search size={48} className="mx-auto mb-3 text-gray-300" />
                <p className="text-lg">No deals found matching &quot;{q}&quot;</p>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Buyer Information */}
      <Section title="Buyer Information" icon={User} helpText="Enter the primary buyer's personal details">
        <Row>
          <Field label="First Name" name="buyer_first_name" value={data.buyer_first_name} onChange={c} half required icon={User} />
          <Field label="Last Name" name="buyer_last_name" value={data.buyer_last_name} onChange={c} half required icon={User} />
        </Row>
        <Row>
          <Field label="Phone Number" name="buyer_phone" value={data.buyer_phone} onChange={c} half type="phone" icon={Phone} />
          <Field label="Email Address" name="buyer_email" value={data.buyer_email} onChange={c} half type="email" icon={Mail} />
        </Row>
        <Row>
          <Field label="Social Security #" name="buyer_ssn" value={data.buyer_ssn} onChange={c} third type="ssn" icon={Hash} />
          <Field label="Date of Birth" name="buyer_dob" value={data.buyer_dob} onChange={c} third type="date" icon={Calendar} />
          <Field label="Marital Status" name="buyer_marital_status" value={data.buyer_marital_status} onChange={c} third />
        </Row>
      </Section>

      {/* Co-Buyer */}
      <Section title="Co-Buyer (Optional)" icon={User} open={false}
        badge={data.co_buyer_first_name ? <Badge color="green">Added</Badge> : null}>
        <Row>
          <Field label="First Name" name="co_buyer_first_name" value={data.co_buyer_first_name} onChange={c} half icon={User} />
          <Field label="Last Name" name="co_buyer_last_name" value={data.co_buyer_last_name} onChange={c} half icon={User} />
        </Row>
        <Row>
          <Field label="Phone" name="co_buyer_phone" value={data.co_buyer_phone} onChange={c} third type="phone" icon={Phone} />
          <Field label="SSN" name="co_buyer_ssn" value={data.co_buyer_ssn} onChange={c} third type="ssn" icon={Hash} />
          <Field label="Marital Status" name="co_buyer_marital_status" value={data.co_buyer_marital_status} onChange={c} third />
        </Row>
      </Section>

      {/* Mailing Address */}
      <Section title="Mailing Address" icon={MapPin} open={false}>
        <Row>
          <Field label="Street Address" name="mailing_address" value={data.mailing_address} onChange={c} icon={MapPinned} />
        </Row>
        <Row>
          <Field label="City" name="mailing_city" value={data.mailing_city} onChange={c} third />
          <Field label="State" name="mailing_state" value={data.mailing_state} onChange={c} third />
          <Field label="ZIP Code" name="mailing_zip" value={data.mailing_zip} onChange={c} third />
        </Row>
      </Section>

      {/* Employment */}
      <Section title="Employment Information" icon={Briefcase} open={false}>
        <Row>
          <Field label="Employer Name" name="employer_name" value={data.employer_name} onChange={c} half icon={Building2} />
          <Field label="Occupation" name="occupation" value={data.occupation} onChange={c} half />
        </Row>
        <Row>
          <Field label="Length of Employment" name="occupation_length" value={data.occupation_length} onChange={c} half />
          <Field label="Work Phone" name="work_phone" value={data.work_phone} onChange={c} half type="phone" icon={Phone} />
        </Row>
      </Section>

      {/* Next Button */}
      <div className="flex justify-end pt-4">
        <BigButton
          onClick={onNext}
          disabled={!canProceed}
          icon={ArrowRight}
        >
          Continue to Home Selection
        </BigButton>
      </div>

      {!canProceed && (
        <div className="text-center text-amber-600 bg-amber-50 rounded-xl p-4">
          <Info size={20} className="inline mr-2" />
          Please enter buyer&apos;s first and last name to continue
        </div>
      )}
    </div>
  );
}

/* ─── Step 2: Choose Home from Inventory ─────────────────── */

function Step2({ data, onChange, inventory, inventoryLoading, onNext, onBack, validationErrors }) {
  const [filter, setFilter] = useState('all'); // all, new, used
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedHome, setSelectedHome] = useState(null);

  const c = (n, v) => onChange(n, v);

  // Filter inventory
  const filteredInventory = (inventory || []).filter(home => {
    if (filter === 'new' && !home.is_new) return false;
    if (filter === 'used' && home.is_new) return false;
    if (searchTerm) {
      const search = searchTerm.toLowerCase();
      return (
        (home.model_name || '').toLowerCase().includes(search) ||
        (home.manufacturer || '').toLowerCase().includes(search) ||
        (home.serial_number || '').toLowerCase().includes(search)
      );
    }
    return true;
  }).slice(0, 12);

  const handleSelectHome = (home) => {
    setSelectedHome(home);
    // Auto-fill home details
    c('is_new', home.is_new !== false);
    c('manufacturer', home.manufacturer || '');
    c('model', home.model_name || '');
    c('year', home.year || '');
    c('serial_number_1', home.serial_number || '');
    c('label_number_1', home.label_number || '');
    c('no_of_sections', home.sections || '');
    if (home.sale_price) {
      c('sales_price', String(home.sale_price));
    }
  };

  const handleClearSelection = () => {
    setSelectedHome(null);
    c('manufacturer', '');
    c('model', '');
    c('year', '');
    c('serial_number_1', '');
    c('serial_number_2', '');
    c('label_number_1', '');
    c('label_number_2', '');
    c('no_of_sections', '');
  };

  const canProceed = data.manufacturer && data.model;

  return (
    <div className="space-y-6">
      {/* Validation Errors */}
      <ValidationErrors errors={validationErrors} />
      
      {/* Header */}
      <div className="text-center mb-6">
        <h2 className="text-2xl font-bold text-gray-800">Select a Home from Inventory</h2>
        <p className="text-gray-600 mt-2">Choose a home to auto-fill the details, or enter manually</p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex gap-2">
          <button
            onClick={() => setFilter('all')}
            className={`px-4 py-2 rounded-xl font-bold text-sm transition-all ${
              filter === 'all' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            All Homes
          </button>
          <button
            onClick={() => setFilter('new')}
            className={`px-4 py-2 rounded-xl font-bold text-sm transition-all ${
              filter === 'new' ? 'bg-green-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            New Homes
          </button>
          <button
            onClick={() => setFilter('used')}
            className={`px-4 py-2 rounded-xl font-bold text-sm transition-all ${
              filter === 'used' ? 'bg-amber-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            Pre-Owned
          </button>
        </div>

        <div className="relative flex-1 max-w-md">
          <Search size={20} className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            placeholder="Search by model, manufacturer, or serial..."
            className="w-full pl-12 pr-4 py-3 border-2 border-gray-300 rounded-xl focus:ring-4 focus:ring-blue-200 focus:border-blue-500 outline-none"
          />
        </div>
      </div>

      {/* Inventory Grid */}
      {inventoryLoading ? (
        <div className="flex flex-col items-center justify-center py-16">
          <Loader2 size={48} className="animate-spin text-blue-600 mb-4" />
          <p className="text-lg text-gray-600">Loading inventory...</p>
        </div>
      ) : filteredInventory.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredInventory.map(home => (
            <button
              key={home.id}
              onClick={() => handleSelectHome(home)}
              className={`
                text-left rounded-xl border-2 p-4 transition-all
                ${selectedHome?.id === home.id
                  ? 'border-blue-500 bg-blue-50 ring-4 ring-blue-200'
                  : 'border-gray-200 bg-white hover:border-blue-300 hover:shadow-md'
                }
              `}
            >
              {home.image_url && (
                <div className="aspect-video bg-gray-100 rounded-lg mb-3 overflow-hidden">
                  <img
                    src={home.image_url}
                    alt={home.model_name}
                    className="w-full h-full object-cover"
                    onError={e => { e.target.style.display = 'none'; }}
                  />
                </div>
              )}

              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-bold text-gray-800">{home.model_name}</h3>
                  <p className="text-sm text-gray-600">{home.manufacturer}</p>
                </div>
                <Badge color={home.is_new !== false ? 'green' : 'amber'}>
                  {home.is_new !== false ? 'New' : 'Pre-Owned'}
                </Badge>
              </div>

              <div className="mt-3 flex items-center gap-4 text-sm text-gray-600">
                {home.beds && <span>{home.beds} bed</span>}
                {home.baths && <span>{home.baths} bath</span>}
                {home.sqft && <span>{home.sqft} sqft</span>}
              </div>

              {home.sale_price && (
                <div className="mt-3 font-bold text-green-700">
                  ${Number(home.sale_price).toLocaleString()}
                </div>
              )}

              {home.serial_number && (
                <div className="mt-2 text-xs text-gray-400">
                  Serial: {home.serial_number.slice(-8)}
                </div>
              )}

              {selectedHome?.id === home.id && (
                <div className="mt-3 flex items-center gap-2 text-blue-600 font-bold">
                  <CheckCircle size={18} />
                  Selected
                </div>
              )}
            </button>
          ))}
        </div>
      ) : (
        <div className="text-center py-16 bg-gray-50 rounded-xl">
          <Home size={64} className="mx-auto mb-4 text-gray-300" />
          <p className="text-lg text-gray-600">No homes found matching your criteria</p>
          <button
            onClick={() => { setFilter('all'); setSearchTerm(''); }}
            className="mt-4 text-blue-600 font-bold hover:underline"
          >
            Clear filters
          </button>
        </div>
      )}

      {/* Divider */}
      <div className="flex items-center gap-4 py-4">
        <div className="flex-1 h-px bg-gray-300" />
        <span className="text-gray-500 font-medium">OR ENTER MANUALLY</span>
        <div className="flex-1 h-px bg-gray-300" />
      </div>

      {/* Manual Entry */}
      <Card className="p-6">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
            <Home size={22} className="text-blue-600" />
            Home Details
          </h3>
          {selectedHome && (
            <button
              onClick={handleClearSelection}
              className="flex items-center gap-2 text-red-600 font-bold hover:text-red-700"
            >
              <X size={18} />
              Clear Selection
            </button>
          )}
        </div>

        {/* Home Type Toggle */}
        <div className="flex gap-3 mb-6">
          <button
            type="button"
            onClick={() => c('is_new', true)}
            className={`flex-1 py-3 px-4 rounded-xl font-bold text-center transition-all ${
              data.is_new
                ? 'bg-green-600 text-white shadow-lg shadow-green-200'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            ✓ New Home
          </button>
          <button
            type="button"
            onClick={() => c('is_new', false)}
            className={`flex-1 py-3 px-4 rounded-xl font-bold text-center transition-all ${
              !data.is_new
                ? 'bg-amber-600 text-white shadow-lg shadow-amber-200'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            ✓ Pre-Owned
          </button>
        </div>

        <Row>
          <Field label="Manufacturer" name="manufacturer" value={data.manufacturer} onChange={c} half required icon={Building2} />
          <Field label="Model Name" name="model" value={data.model} onChange={c} half required icon={Home} />
        </Row>
        <Row>
          <Field label="Year" name="year" value={data.year} onChange={c} third />
          <Field label="Serial # 1" name="serial_number_1" value={data.serial_number_1} onChange={c} third required icon={Hash} />
          <Field label="Serial # 2" name="serial_number_2" value={data.serial_number_2} onChange={c} third icon={Hash} />
        </Row>
        <Row>
          <Field label="Label # 1" name="label_number_1" value={data.label_number_1} onChange={c} third />
          <Field label="Label # 2" name="label_number_2" value={data.label_number_2} onChange={c} third />
          <Field label="# of Sections" name="no_of_sections" value={data.no_of_sections} onChange={c} third />
        </Row>
      </Card>

      {/* Installation Site */}
      <Section title="Installation Site Address" icon={MapPin}>
        <Row>
          <Field label="Street Address" name="buyer_address" value={data.buyer_address} onChange={c} required icon={MapPinned} />
        </Row>
        <Row>
          <Field label="City" name="buyer_city" value={data.buyer_city} onChange={c} third required />
          <Field label="County" name="buyer_county" value={data.buyer_county} onChange={c} third />
          <Field label="State" name="buyer_state" value={data.buyer_state} onChange={c} third />
        </Row>
        <Row>
          <Field label="ZIP Code" name="buyer_zip" value={data.buyer_zip} onChange={c} half />
        </Row>
      </Section>

      {/* Pricing */}
      <Section title="Pricing Information" icon={BadgeDollarSign}>
        <Row>
          <Field label="Sales Price" name="sales_price" value={data.sales_price} onChange={c} third type="currency" required icon={DollarSign} />
          <Field label="Down Payment" name="down_payment" value={data.down_payment} onChange={c} third type="currency" icon={DollarSign} />
          <Field
            label="Unpaid Balance"
            name="_ub"
            value={data.sales_price ? (parseFloat(data.sales_price) - parseFloat(data.down_payment || 0)).toFixed(2) : ''}
            onChange={() => { }}
            third
            readOnly
            icon={DollarSign}
          />
        </Row>
      </Section>

      {/* Financing */}
      <Section title="Financing Details (Optional)" icon={CreditCard} open={false}>
        <Row>
          <Field label="Creditor Name" name="creditor_name" value={data.creditor_name} onChange={c} half />
          <Field label="Creditor Phone" name="creditor_phone" value={data.creditor_phone} onChange={c} half type="phone" icon={Phone} />
        </Row>
        <Row>
          <Field label="Loan Term (months)" name="loan_term" value={data.loan_term} onChange={c} third />
          <Field label="APR (%)" name="apr" value={data.apr} onChange={c} third />
          <Field label="Monthly Payment" name="monthly_payment" value={data.monthly_payment} onChange={c} third type="currency" icon={DollarSign} />
        </Row>
        <Row>
          <Field label="Sales Representative" name="salesrep" value={data.salesrep} onChange={c} half />
          <Field label="Payment Start Date" name="payment_start_date" value={data.payment_start_date} onChange={c} half type="date" icon={Calendar} />
        </Row>
      </Section>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-6">
        <BigButton variant="secondary" onClick={onBack} icon={ArrowLeft}>
          Back to Customer Info
        </BigButton>
        <BigButton onClick={onNext} disabled={!canProceed} icon={ArrowRight}>
          Continue to Documents
        </BigButton>
      </div>

      {!canProceed && (
        <div className="text-center text-amber-600 bg-amber-50 rounded-xl p-4 mt-4">
          <Info size={20} className="inline mr-2" />
          Please enter at least manufacturer and model to continue
        </div>
      )}
    </div>
  );
}

/* ─── Step 3: Select Documents ───────────────────────────── */

function Step3({ templates, packets, selected, onToggle, onSelectPacket, isNew, onNext, onBack }) {
  const [expandedCats, setExpandedCats] = useState({ TMHA: true, TDHCA: false, State: false, Internal: false });
  const [viewMode, setViewMode] = useState('packets'); // packets, individual

  // Group templates by category
  const byCat = {};
  (templates || []).forEach(t => {
    const c = t.category || 'Other';
    (byCat[c] = byCat[c] || []).push(t);
  });

  const sel = new Set(selected);

  // Sort packets: recommended first
  const sortedPackets = [...(packets || [])].sort((a, b) => {
    const aRec = isNew ? (a.packet_name === 'standard_closing' || a.packet_name.includes('new')) : a.packet_name.includes('used');
    const bRec = isNew ? (b.packet_name === 'standard_closing' || b.packet_name.includes('new')) : b.packet_name.includes('used');
    return bRec - aRec;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center mb-6">
        <h2 className="text-2xl font-bold text-gray-800">Select Documents to Generate</h2>
        <p className="text-gray-600 mt-2">Choose a pre-made packet or select individual documents</p>
      </div>

      {/* View Toggle */}
      <div className="flex justify-center gap-2 mb-6">
        <button
          onClick={() => setViewMode('packets')}
          className={`px-6 py-3 rounded-xl font-bold text-sm transition-all ${
            viewMode === 'packets'
              ? 'bg-blue-600 text-white shadow-lg'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          <Package size={18} className="inline mr-2" />
          Document Packets
        </button>
        <button
          onClick={() => setViewMode('individual')}
          className={`px-6 py-3 rounded-xl font-bold text-sm transition-all ${
            viewMode === 'individual'
              ? 'bg-blue-600 text-white shadow-lg'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          <FileText size={18} className="inline mr-2" />
          Individual Documents
        </button>
      </div>

      {/* Packets View */}
      {viewMode === 'packets' && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {sortedPackets.map(p => {
              const isRec = isNew
                ? (p.packet_name === 'standard_closing' || p.packet_name.includes('new'))
                : p.packet_name.includes('used');
              const cnt = (p.templates || []).length;
              const selectedCount = (p.templates || []).filter(t => sel.has(t)).length;
              const isFull = selectedCount === cnt && cnt > 0;

              return (
                <button
                  key={p.packet_name}
                  onClick={() => onSelectPacket(p)}
                  className={`
                    relative text-left p-6 rounded-xl border-2 transition-all
                    ${isFull
                      ? 'border-blue-500 bg-blue-50 shadow-md'
                      : 'border-gray-200 bg-white hover:border-blue-300 hover:shadow-md'
                    }
                  `}
                >
                  {isRec && (
                    <span className="absolute -top-3 left-4 bg-blue-600 text-white text-xs font-bold px-3 py-1 rounded-full">
                      RECOMMENDED
                    </span>
                  )}

                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="font-bold text-gray-800 text-lg">{p.display_name}</h3>
                      <p className="text-sm text-gray-500 mt-1">{cnt} documents included</p>
                    </div>
                    {isFull && <CheckCircle size={28} className="text-blue-600" />}
                  </div>

                  {p.description && (
                    <p className="text-sm text-gray-600 mt-3">{p.description}</p>
                  )}

                  {selectedCount > 0 && !isFull && (
                    <div className="mt-3 text-sm text-blue-600 font-medium">
                      {selectedCount} of {cnt} selected
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          <div className="text-center">
            <button
              onClick={() => setViewMode('individual')}
              className="text-blue-600 font-bold hover:underline"
            >
              Need specific documents? Browse individual files →
            </button>
          </div>
        </div>
      )}

      {/* Individual Documents View */}
      {viewMode === 'individual' && (
        <div className="space-y-4">
          {CAT_ORDER.map(cat => {
            const docs = byCat[cat];
            if (!docs?.length) return null;

            const selectedCount = docs.filter(d => sel.has(d.template_name)).length;
            const isExpanded = expandedCats[cat] ?? false;
            const Icon = CAT_ICONS[cat] || FolderOpen;

            return (
              <div key={cat} className="bg-white rounded-xl border-2 border-gray-200 overflow-hidden">
                <button
                  onClick={() => setExpandedCats(p => ({ ...p, [cat]: !p[cat] }))}
                  className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${CAT_COLORS[cat]}`}>
                      <Icon size={20} />
                    </div>
                    <div>
                      <span className={`text-xs font-bold px-2 py-1 rounded border ${CAT_COLORS[cat]}`}>
                        {cat}
                      </span>
                      <span className="ml-3 font-bold text-gray-700">{docs.length} documents</span>
                    </div>
                    {selectedCount > 0 && (
                      <Badge color="blue">{selectedCount} selected</Badge>
                    )}
                  </div>
                  {isExpanded ? <ChevronDown size={22} /> : <ChevronRight size={22} />}
                </button>

                {isExpanded && (
                  <div className="border-t border-gray-200 divide-y divide-gray-100">
                    {docs.map(doc => {
                      const isSelected = sel.has(doc.template_name);
                      return (
                        <label
                          key={doc.template_name}
                          className={`
                            flex items-center px-6 py-4 cursor-pointer transition-colors
                            ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}
                          `}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => onToggle(doc.template_name)}
                            className="w-5 h-5 rounded border-gray-300 text-blue-600 focus:ring-blue-500 mr-4"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-gray-800">{doc.display_name}</div>
                            {doc.description && (
                              <div className="text-sm text-gray-500 mt-0.5">{doc.description}</div>
                            )}
                          </div>
                          {isSelected && <CheckCircle size={20} className="text-blue-600 ml-2" />}
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Summary */}
      <div className="bg-blue-50 rounded-xl border-2 border-blue-200 p-6">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-gray-700 font-medium">
              <span className="text-2xl font-bold text-blue-700">{selected.length}</span>
              {' '}document{selected.length !== 1 ? 's' : ''} selected
            </span>
            {selected.length > 0 && (
              <button
                onClick={() => selected.forEach(s => onToggle(s))}
                className="ml-4 text-red-600 font-bold hover:text-red-700 text-sm"
              >
                Clear All
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-6">
        <BigButton variant="secondary" onClick={onBack} icon={ArrowLeft}>
          Back to Home Selection
        </BigButton>
        <BigButton
          onClick={onNext}
          disabled={selected.length === 0}
          icon={FileText}
        >
          Generate {selected.length} Document{selected.length !== 1 ? 's' : ''}
        </BigButton>
      </div>
    </div>
  );
}

/* ─── Step 4: Generate & Download ────────────────────────── */

function Step4({ results, generating, error, onBack, onReset }) {
  if (generating) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mb-6">
          <Loader2 size={40} className="animate-spin text-blue-600" />
        </div>
        <h2 className="text-2xl font-bold text-gray-800 mb-2">Generating Documents...</h2>
        <p className="text-gray-500 text-lg">This may take a moment for larger packets</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="w-20 h-20 bg-red-100 rounded-full flex items-center justify-center mb-6">
          <AlertCircle size={40} className="text-red-500" />
        </div>
        <h2 className="text-2xl font-bold text-gray-800 mb-2">Generation Failed</h2>
        <p className="text-red-500 text-lg mb-6">{error}</p>
        <BigButton onClick={onBack} variant="secondary" icon={ArrowLeft}>
          Go Back & Try Again
        </BigButton>
      </div>
    );
  }

  if (!results) return null;

  const ok = (results.documents || []).filter(d => d.success);
  const fail = (results.documents || []).filter(d => !d.success);

  return (
    <div className="space-y-6">
      {/* Success Header */}
      <div className="bg-green-50 border-2 border-green-200 rounded-xl p-8 text-center">
        <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <CheckCircle size={40} className="text-green-600" />
        </div>
        <h2 className="text-2xl font-bold text-green-800 mb-2">
          {results.successful} of {results.total} Documents Ready!
        </h2>
        {results.merged && (
          <p className="text-green-600">
            Combined PDF with {results.merged.page_count} pages
          </p>
        )}
      </div>

      {/* Download All Button */}
      {results.merged && (
        <a
          href={results.merged.download_url}
          download
          className="flex items-center justify-center gap-3 w-full py-5 bg-blue-600 text-white rounded-xl font-bold text-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200"
        >
          <Download size={28} />
          Download All Documents ({results.merged.page_count} pages)
        </a>
      )}

      {/* Individual Documents */}
      <div className="space-y-3">
        <h3 className="text-lg font-bold text-gray-700 flex items-center gap-2">
          <FileText size={20} className="text-gray-400" />
          Individual Documents
        </h3>

        {ok.map(d => (
          <div
            key={d.template_name}
            className="flex items-center justify-between bg-white border-2 border-gray-200 rounded-xl px-6 py-4 hover:border-blue-300 transition-colors"
          >
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center flex-shrink-0">
                <FileCheck size={20} className="text-green-600" />
              </div>
              <span className="font-medium text-gray-800 truncate">{d.display_name}</span>
            </div>
            <a
              href={d.download_url}
              download
              className="flex items-center gap-2 px-4 py-2 bg-blue-100 text-blue-700 rounded-lg font-bold hover:bg-blue-200 transition-colors flex-shrink-0"
            >
              <Download size={18} />
              PDF
            </a>
          </div>
        ))}

        {fail.length > 0 && (
          <>
            <h4 className="text-sm font-bold text-red-500 mt-6">Failed ({fail.length})</h4>
            {fail.map(d => (
              <div
                key={d.template_name}
                className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl px-6 py-3"
              >
                <AlertCircle size={18} className="text-red-500 flex-shrink-0" />
                <span className="text-gray-700">{d.display_name}</span>
                <span className="text-xs text-red-400 ml-auto">{d.message}</span>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between pt-6">
        <BigButton variant="secondary" onClick={onBack} icon={ArrowLeft}>
          Back to Selection
        </BigButton>
        <BigButton onClick={onReset} icon={RotateCcw}>
          Start New Document
        </BigButton>
      </div>
    </div>
  );
}

/* ─── Form Validation ────────────────────────────────────── */

function validateForm(form, step) {
  const errors = [];
  
  if (step === 1) {
    if (!form.buyer_first_name?.trim()) errors.push('Buyer first name is required');
    if (!form.buyer_last_name?.trim()) errors.push('Buyer last name is required');
    if (form.buyer_email && !form.buyer_email.includes('@')) errors.push('Invalid email address');
    if (form.buyer_phone && form.buyer_phone.length < 10) errors.push('Phone number should be at least 10 digits');
  }
  
  if (step === 2) {
    if (!form.manufacturer?.trim()) errors.push('Manufacturer is required');
    if (!form.model?.trim()) errors.push('Model name is required');
    if (!form.serial_number_1?.trim()) errors.push('Serial number is required');
    if (form.sales_price && isNaN(parseFloat(form.sales_price))) errors.push('Sales price must be a number');
  }
  
  return errors;
}

/* ─── Main Component ─────────────────────────────────────── */

export default function DocumentCenter() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({ ...INITIAL_FORM });
  const [selDocs, setSelDocs] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [packets, setPackets] = useState([]);
  const [deals, setDeals] = useState([]);
  const [dealsLoading, setDealsLoading] = useState(false);
  const [inventory, setInventory] = useState([]);
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [results, setResults] = useState(null);
  const [genErr, setGenErr] = useState('');
  const [validationErrors, setValidationErrors] = useState([]);
  const [lastSaved, setLastSaved] = useState(null);
  const [showDuplicateWarning, setShowDuplicateWarning] = useState(null);

  // Load initial data
  useEffect(() => {
    adminFetch('/api/documents/templates')
      .then(r => r.json())
      .then(d => {
        setTemplates(d.templates || []);
        setPackets(d.packets || []);
      })
      .catch(() => { });

    setDealsLoading(true);
    adminFetch('/api/deals?limit=100')
      .then(r => r.json())
      .then(d => setDeals(d.deals || []))
      .catch(() => { })
      .finally(() => setDealsLoading(false));

    // Load inventory for Step 2
    setInventoryLoading(true);
    adminFetch('/api/inventory?limit=100&status=AVAILABLE')
      .then(r => r.json())
      .then(d => setInventory(d.inventory || []))
      .catch(() => { })
      .finally(() => setInventoryLoading(false));
    
    // Load draft from localStorage
    const saved = localStorage.getItem('document_center_draft');
    if (saved) {
      try {
        const draft = JSON.parse(saved);
        if (draft.form) setForm(draft.form);
        if (draft.selDocs) setSelDocs(draft.selDocs);
        if (draft.step) setStep(draft.step);
        setLastSaved(new Date(draft.timestamp));
      } catch (e) {
        console.error('Failed to load draft:', e);
      }
    }
  }, []);

  // Auto-save to localStorage
  useEffect(() => {
    const timeout = setTimeout(() => {
      const draft = {
        form,
        selDocs,
        step,
        timestamp: new Date().toISOString()
      };
      localStorage.setItem('document_center_draft', JSON.stringify(draft));
      setLastSaved(new Date());
    }, 3000); // Save 3 seconds after last change
    
    return () => clearTimeout(timeout);
  }, [form, selDocs, step]);

  const chg = useCallback((n, v) => {
    setForm(p => ({ ...p, [n]: v }));
    setValidationErrors([]); // Clear errors on change
    
    // Check for duplicate deals when buyer name changes
    if (n === 'buyer_first_name' || n === 'buyer_last_name' || n === 'buyer_phone') {
      checkForDuplicates(n === 'buyer_first_name' ? v : form.buyer_first_name, 
                         n === 'buyer_last_name' ? v : form.buyer_last_name,
                         n === 'buyer_phone' ? v : form.buyer_phone);
    }
  }, [form, deals]);
  
  // Check for duplicate deals
  const checkForDuplicates = (firstName, lastName, phone) => {
    if (!firstName && !lastName && !phone) {
      setShowDuplicateWarning(null);
      return;
    }
    
    const matches = deals.filter(d => {
      const nameMatch = firstName && lastName && 
        d.buyer_first_name?.toLowerCase() === firstName.toLowerCase() &&
        d.buyer_last_name?.toLowerCase() === lastName.toLowerCase();
      const phoneMatch = phone && d.buyer_phone === phone.replace(/\D/g, '');
      return nameMatch || phoneMatch;
    });
    
    if (matches.length > 0) {
      setShowDuplicateWarning({
        count: matches.length,
        deals: matches.slice(0, 3)
      });
    } else {
      setShowDuplicateWarning(null);
    }
  };
  
  // Clear draft
  const clearDraft = () => {
    localStorage.removeItem('document_center_draft');
    setForm({ ...INITIAL_FORM });
    setSelDocs([]);
    setStep(1);
    setLastSaved(null);
  };

  const loadDeal = useCallback(d => {
    const m = { ...INITIAL_FORM };
    Object.keys(INITIAL_FORM).forEach(k => {
      if (d[k] != null) m[k] = d[k];
    });
    setForm(m);
  }, []);

  const toggleDoc = useCallback(t => {
    setSelDocs(p => p.includes(t) ? p.filter(x => x !== t) : [...p, t]);
  }, []);

  const selectPacket = useCallback(pk => {
    const tpls = pk.templates || [];
    setSelDocs(p => {
      const s = new Set(p);
      if (tpls.every(t => s.has(t))) {
        return p.filter(t => !tpls.includes(t));
      }
      tpls.forEach(t => s.add(t));
      return [...s];
    });
  }, []);

  const generate = async () => {
    setGenerating(true);
    setGenErr('');
    setResults(null);
    setStep(4);

    try {
      const r = await adminFetch('/api/documents/generate-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          templates: selDocs,
          data: toDocumentData(form),
          merge: true
        }),
      });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      setResults(d);
    } catch (e) {
      setGenErr(e.message || 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const reset = () => {
    setStep(1);
    setForm({ ...INITIAL_FORM });
    setSelDocs([]);
    setResults(null);
    setGenErr('');
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
              <div className="w-12 h-12 bg-blue-600 rounded-xl flex items-center justify-center">
                <FileText size={28} className="text-white" />
              </div>
              Document Center
            </h1>
            <p className="text-gray-500 mt-2 text-lg">Generate sales contracts, closing packets, and more</p>
          </div>
          
          {/* Auto-save indicator and clear draft */}
          <div className="flex items-center gap-3">
            {lastSaved && (
              <span className="text-sm text-gray-400">
                Auto-saved {lastSaved.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
            {step < 4 && (
              <button
                onClick={clearDraft}
                className="text-sm text-gray-400 hover:text-red-600 transition-colors flex items-center gap-1"
                title="Clear all fields and start over"
              >
                <RotateCcw size={14} />
                Reset
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Step Bar */}
      <StepBar step={step} />

      {/* Steps */}
      {step === 1 && (
        <Step1
          data={form}
          onChange={chg}
          deals={deals}
          dealsLoading={dealsLoading}
          onLoadDeal={loadDeal}
          onNext={() => {
            const errors = validateForm(form, 1);
            if (errors.length > 0) {
              setValidationErrors(errors);
            } else {
              setValidationErrors([]);
              setStep(2);
            }
          }}
          validationErrors={validationErrors}
          duplicateWarning={showDuplicateWarning}
          onViewDuplicate={(deal) => {
            loadDeal(deal);
            setShowDuplicateWarning(null);
          }}
        />
      )}

      {step === 2 && (
        <Step2
          data={form}
          onChange={chg}
          inventory={inventory}
          inventoryLoading={inventoryLoading}
          onNext={() => {
            const errors = validateForm(form, 2);
            if (errors.length > 0) {
              setValidationErrors(errors);
            } else {
              setValidationErrors([]);
              setStep(3);
            }
          }}
          onBack={() => setStep(1)}
          validationErrors={validationErrors}
        />
      )}

      {step === 3 && (
        <Step3
          templates={templates}
          packets={packets}
          selected={selDocs}
          onToggle={toggleDoc}
          onSelectPacket={selectPacket}
          isNew={form.is_new}
          onNext={generate}
          onBack={() => setStep(2)}
        />
      )}

      {step === 4 && (
        <Step4
          results={results}
          generating={generating}
          error={genErr}
          onBack={() => setStep(3)}
          onReset={reset}
        />
      )}
    </div>
  );
}
