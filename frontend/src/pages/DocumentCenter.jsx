import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  FileText, Download, CheckCircle, AlertCircle, Search,
  Loader2, ChevronRight, ChevronDown, Home, Plus, X,
  Package, User, DollarSign, Briefcase, MapPin,
  ArrowRight, ArrowLeft, RotateCcw, Check, FileCheck,
  Building2, Phone, Mail, Calendar, Hash, MapPinned,
  CreditCard, BadgeDollarSign, ClipboardList, FolderOpen,
  Info, HelpCircle, Calculator, RefreshCw, ShieldCheck, Clock
} from 'lucide-react';
import adminFetch from '../adminFetch';
import downloadAdminFile from '../downloadAdminFile';

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

const hasValue = v => v !== undefined && v !== null && String(v).trim() !== '';

const joinName = (first, last) => [first, last].filter(hasValue).map(v => String(v).trim()).join(' ') || undefined;

const cityStateZip = (city, state, zip) => {
  if (!hasValue(city) && !hasValue(zip)) return undefined;
  const cityPart = hasValue(city) ? `${String(city).trim()}, ` : '';
  const statePart = hasValue(state) ? String(state).trim() : 'TX';
  const zipPart = hasValue(zip) ? ` ${String(zip).trim()}` : '';
  return `${cityPart}${statePart}${zipPart}`.trim();
};

const fullAddress = (address, cityZip) => {
  if (!hasValue(address)) return undefined;
  return hasValue(cityZip) ? `${String(address).trim()}, ${cityZip}` : String(address).trim();
};

const joinNonEmpty = (parts, separator = ' | ') => {
  const clean = parts.filter(hasValue).map(v => String(v).trim());
  return clean.length ? clean.join(separator) : undefined;
};

const statusFlags = (status, prefix) => {
  const s = String(status || '').trim().toLowerCase();
  if (s === 'married') return { [`${prefix}_married`]: true };
  if (s === 'single' || s === 'unmarried') return { [`${prefix}_unmarried`]: true };
  if (s === 'separated') return { [`${prefix}_separated`]: true };
  return {};
};

function toDocumentData(f) {
  const buyer = joinName(f.buyer_first_name, f.buyer_last_name);
  const coBuyer = joinName(f.co_buyer_first_name, f.co_buyer_last_name);
  const buyerCityStateZip = cityStateZip(f.buyer_city, f.buyer_state, f.buyer_zip);
  const mailingCityStateZip = cityStateZip(f.mailing_city, f.mailing_state, f.mailing_zip);
  const serialLabelCombined = joinNonEmpty([
    hasValue(f.serial_number_1) ? `S/N: ${f.serial_number_1}` : null,
    hasValue(f.serial_number_2) ? `S/N2: ${f.serial_number_2}` : null,
    hasValue(f.label_number_1) ? `HUD: ${f.label_number_1}` : null,
    hasValue(f.label_number_2) ? `HUD2: ${f.label_number_2}` : null,
  ]);
  const ownRent = String(f.mailing_own_rent || '').trim().toLowerCase();
  return {
    ...f,
    buyer_name: buyer,
    co_buyer_name: coBuyer,
    buyer_city_state_zip: buyerCityStateZip,
    buyer_full_address: fullAddress(f.buyer_address, buyerCityStateZip),
    mailing_city_state_zip: mailingCityStateZip,
    mailing_full_address: fullAddress(f.mailing_address, mailingCityStateZip),
    manufacturer_model: joinNonEmpty([f.manufacturer, f.model], ' '),
    serial_label_combined: serialLabelCombined,
    buyer_owns_residence: ['own', 'owns', 'o'].includes(ownRent) || undefined,
    buyer_rents_residence: ['rent', 'rents', 'r'].includes(ownRent) || undefined,
    ...statusFlags(f.buyer_marital_status, 'buyer'),
    ...statusFlags(f.co_buyer_marital_status, 'co_buyer'),
  };
}

function formatBytes(bytes) {
  if (!bytes) return '0 KB';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value) {
  if (!value) return 'Not yet';
  try {
    return new Date(value).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit'
    });
  } catch {
    return value;
  }
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

/**
 * Field — uses uncontrolled input (defaultValue) to ELIMINATE focus loss.
 *
 * React controlled inputs (value={state}) re-render on every keystroke,
 * which causes focus loss when parent state changes. This component uses
 * defaultValue + onChange → parent ref update (no state, no re-render).
 *
 * The input DOM element owns its own value. React never touches it during typing.
 * We use a key={name + '-' + resetKey} to force re-mount only when loading a customer.
 */
const Field = React.memo(function Field({ label, name, value, onChange, type = 'text', placeholder, half, third, required, readOnly, icon: Icon, resetKey, error, autoFilled, helperText }) {
  const inputRef = React.useRef(null);
  const borderClass = readOnly
    ? 'bg-gray-100 text-gray-500 border-gray-200'
    : error
      ? 'bg-white border-red-400 hover:border-red-500'
      : autoFilled
        ? 'bg-green-50 border-green-300 hover:border-green-400'
        : 'bg-white border-gray-300 hover:border-gray-400';
  const focusRingClass = error
    ? 'focus:ring-red-200 focus:border-red-500'
    : 'focus:ring-blue-200 focus:border-blue-500';
  const inputClasses = `
    w-full px-4 py-3 border-2 rounded-xl text-base transition-all
    ${focusRingClass} outline-none
    ${borderClass}
  `;

  const widthClass = third ? 'w-full sm:w-1/3' : half ? 'w-full sm:w-1/2' : 'w-full';

  return (
    <div className={`${widthClass} px-2 mb-4`}>
      <label className="block text-sm font-bold text-gray-700 mb-2">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
        {autoFilled && (
          <span
            data-testid={`autofilled-${name}`}
            className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-semibold"
          >
            <Check size={12} /> from inventory
          </span>
        )}
      </label>
      <div className="relative">
        {Icon && <Icon size={18} className={`absolute left-3 top-1/2 -translate-y-1/2 ${error ? 'text-red-400' : 'text-gray-400'}`} />}
        <input
          key={`${name}-${resetKey || 0}`}
          ref={inputRef}
          type={type === 'ssn' ? 'password' : type === 'currency' ? 'text' : type}
          inputMode={type === 'currency' ? 'decimal' : type === 'phone' ? 'tel' : undefined}
          name={name}
          defaultValue={value || ''}
          onChange={e => onChange(name, e.target.value)}
          placeholder={placeholder}
          readOnly={readOnly}
          aria-invalid={error ? 'true' : undefined}
          className={`${inputClasses} ${Icon ? 'pl-10' : ''}`}
        />
      </div>
      {error && typeof error === 'string' && (
        <p className="mt-1 text-xs text-red-600 font-medium">{error}</p>
      )}
      {!error && helperText && (
        <p
          data-testid={`helper-${name}`}
          className="mt-1 text-xs text-gray-500 italic"
        >
          {helperText}
        </p>
      )}
    </div>
  );
});

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

function DocumentDesk({ readiness, history, loading, error, downloadingDoc, downloadError, onRefresh, onDownload }) {
  const statusReady = readiness?.status === 'ready';
  const recentDocs = (history || []).slice(0, 4);

  return (
    <div className="mb-8 grid grid-cols-1 lg:grid-cols-[1.1fr_1fr] gap-4">
      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${statusReady ? 'bg-green-100' : 'bg-amber-100'}`}>
              <ShieldCheck size={22} className={statusReady ? 'text-green-700' : 'text-amber-700'} />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-lg font-bold text-gray-900">Production Document Desk</h2>
                {readiness && (
                  <Badge color={statusReady ? 'green' : 'amber'}>
                    {statusReady ? 'Ready' : 'Needs Review'}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-gray-500 mt-1">
                Templates, packets, storage, and recent generated PDFs in one place.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="p-2 rounded-lg border border-gray-200 text-gray-500 hover:text-blue-700 hover:border-blue-300 disabled:opacity-50"
            title="Refresh document status"
            aria-label="Refresh document status"
          >
            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        <div className="mt-5 grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="rounded-xl bg-blue-50 border border-blue-100 p-3">
            <div className="text-2xl font-bold text-blue-800">{readiness?.template_count ?? '—'}</div>
            <div className="text-xs font-medium text-blue-700">Templates</div>
          </div>
          <div className="rounded-xl bg-purple-50 border border-purple-100 p-3">
            <div className="text-2xl font-bold text-purple-800">{readiness?.packet_count ?? '—'}</div>
            <div className="text-xs font-medium text-purple-700">Packets</div>
          </div>
          <div className="rounded-xl bg-green-50 border border-green-100 p-3">
            <div className="text-2xl font-bold text-green-800">{readiness?.generated_document_count ?? '—'}</div>
            <div className="text-xs font-medium text-green-700">Generated</div>
          </div>
          <div className="rounded-xl bg-gray-50 border border-gray-200 p-3">
            <div className="text-2xl font-bold text-gray-800">{readiness?.gcs_document_count ?? '—'}</div>
            <div className="text-xs font-medium text-gray-600">Cloud PDFs</div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 text-xs text-gray-500">
          <span className="inline-flex items-center gap-1">
            <Clock size={13} />
            Latest: {formatDateTime(readiness?.latest_document_at)}
          </span>
          <span>Storage: {readiness?.output_dir || 'checking'}</span>
          {error && <span className="text-red-600">{error}</span>}
        </div>
      </Card>

      <Card className="p-5">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Recent PDFs</h2>
            <p className="text-sm text-gray-500">Quickly re-download generated paperwork.</p>
          </div>
          {loading && <Loader2 size={18} className="animate-spin text-blue-600" />}
        </div>

        {recentDocs.length > 0 ? (
          <div className="space-y-2">
            {recentDocs.map(doc => (
              <button
                type="button"
                key={`${doc.source || 'doc'}-${doc.filename}`}
                onClick={() => onDownload(doc.download_url, doc.filename)}
                disabled={downloadingDoc === doc.download_url}
                className="w-full flex items-center justify-between gap-3 rounded-xl border border-gray-200 px-3 py-2 text-left hover:border-blue-300 hover:bg-blue-50 disabled:opacity-60"
              >
                <span className="min-w-0">
                  <span className="block truncate font-semibold text-gray-800">{doc.filename}</span>
                  <span className="text-xs text-gray-500">
                    {formatDateTime(doc.created_at)} · {formatBytes(doc.size_bytes)}
                  </span>
                </span>
                {downloadingDoc === doc.download_url ? (
                  <Loader2 size={18} className="animate-spin text-blue-600 flex-shrink-0" />
                ) : (
                  <Download size={18} className="text-blue-600 flex-shrink-0" />
                )}
              </button>
            ))}
          </div>
        ) : (
          <div className="rounded-xl bg-gray-50 border border-dashed border-gray-300 p-5 text-center text-sm text-gray-500">
            No generated documents found yet.
          </div>
        )}

        {downloadError && (
          <div className="mt-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
            {downloadError}
          </div>
        )}
      </Card>
    </div>
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

function Step1({ data, onChange, resetKey, deals, dealsLoading, onLoadDeal, onNext, validationErrors, missingFields, duplicateWarning, onViewDuplicate }) {
  const [q, setQ] = useState('');
  const [showPicker, setShowPicker] = useState(false);

  // Legacy customer search (FastContract migration)
  const [custSearch, setCustSearch] = useState('');
  const [custResults, setCustResults] = useState([]);
  const [custLoading, setCustLoading] = useState(false);
  const [showCustPicker, setShowCustPicker] = useState(false);
  const [customerSaveState, setCustomerSaveState] = useState({ status: 'idle', message: '' });

  const searchCustomers = async (query) => {
    if (!query || query.length < 2) { setCustResults([]); return; }
    setCustLoading(true);
    try {
      const res = await adminFetch(`/api/customers/search?q=${encodeURIComponent(query)}&limit=8`);
      if (res.ok) {
        const data = await res.json();
        setCustResults(data.customers || []);
      }
    } catch (e) { console.error('Customer search error:', e); }
    setCustLoading(false);
  };

  const loadCustomer = (cust) => {
    const names = (cust.full_name || '').split(' ');
    const first = names[0] || '';
    const last = names.slice(1).join(' ') || '';
    onChange('buyer_first_name', first);
    onChange('buyer_last_name', last);
    if (cust.phone) onChange('buyer_phone', cust.phone);
    if (cust.email) onChange('buyer_email', cust.email);
    if (cust.ssn_masked) onChange('buyer_ssn', cust.ssn_masked);
    if (cust.employer) onChange('employer_name', cust.employer);
    if (cust.occupation) onChange('occupation', cust.occupation);
    if (cust.salesrep) onChange('salesrep', cust.salesrep);
    if (cust.marital_status) onChange('buyer_marital_status', cust.marital_status);
    // Address
    if (cust.address) {
      const parts = cust.address.split(',').map(s => s.trim());
      if (parts[0]) onChange('mailing_address', parts[0]);
    }
    if (cust.city) onChange('mailing_city', cust.city);
    if (cust.state) onChange('mailing_state', cust.state);
    if (cust.zip_code) onChange('mailing_zip', cust.zip_code);
    // Co-buyer
    if (cust.co_buyer) {
      const coNames = (cust.co_buyer.full_name || '').split(' ');
      onChange('co_buyer_first_name', coNames[0] || '');
      onChange('co_buyer_last_name', coNames.slice(1).join(' ') || '');
      if (cust.co_buyer.phone) onChange('co_buyer_phone', cust.co_buyer.phone);
      if (cust.co_buyer.ssn_masked) onChange('co_buyer_ssn', cust.co_buyer.ssn_masked);
    }
    setShowCustPicker(false);
    setCustSearch('');
  };

  const filtered = (deals || []).filter(d => {
    if (!q) return true;
    const s = q.toLowerCase();
    return `${d.buyer_first_name || ''} ${d.buyer_last_name || ''}`.toLowerCase().includes(s)
      || (d.model || '').toLowerCase().includes(s)
      || (d.id || '').toLowerCase().includes(s);
  }).slice(0, 6);

  const c = (n, v) => onChange(n, v);

  const liveValidation = getValidationState(data, 1);
  const canProceed = liveValidation.missing.size === 0;
  const missing = missingFields || new Set();
  const errOf = (name) => missing.has(name);
  const savingCustomer = customerSaveState.status === 'saving';

  const saveCurrentCustomer = async () => {
    if (!canProceed || savingCustomer) return;
    setCustomerSaveState({ status: 'saving', message: 'Saving customer...' });
    const name = `${data.buyer_first_name} ${data.buyer_last_name}`.trim();
    try {
      const res = await adminFetch('/api/customers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: name,
          email: data.buyer_email || null,
          phone: data.buyer_phone || null,
          address: data.mailing_address || null,
          city: data.mailing_city || null,
          state: data.mailing_state || 'TX',
          zip_code: data.mailing_zip || null,
          employer: data.employer_name || null,
          occupation: data.occupation || null,
          salesrep: data.salesrep || null,
        }),
      });
      if (res.ok) {
        const body = await res.json();
        const saved = body.customer || {};
        setCustomerSaveState({
          status: 'saved',
          message: `${saved.full_name || name} saved to customer records.`,
        });
        return;
      }
      const err = await res.json().catch(() => ({}));
      setCustomerSaveState({
        status: 'error',
        message: `Failed to save customer: ${err.detail || res.statusText}`,
      });
    } catch (e) {
      console.error('Save customer error:', e);
      setCustomerSaveState({
        status: 'error',
        message: 'Failed to save customer. Please try again.',
      });
    }
  };

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

      {/* Load from Legacy Customer (FastContract migration) */}
      <Card className="p-6 bg-gradient-to-r from-green-50 to-emerald-100 border-green-200">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 bg-green-600 rounded-xl flex items-center justify-center">
              <User size={24} className="text-white" />
            </div>
            <div>
              <h3 className="font-bold text-gray-800 text-lg">Load from Customer Records</h3>
              <p className="text-sm text-gray-600">Search saved and migrated customer records</p>
            </div>
          </div>
          <BigButton
            variant="secondary"
            onClick={() => setShowCustPicker(!showCustPicker)}
            icon={showCustPicker ? ChevronDown : ChevronRight}
          >
            {showCustPicker ? 'Hide' : 'Find Customer'}
          </BigButton>
        </div>

        {showCustPicker && (
          <div className="mt-4 pt-4 border-t border-green-200">
            <div className="relative mb-4">
              <Search size={20} className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                value={custSearch}
                onChange={e => { setCustSearch(e.target.value); searchCustomers(e.target.value); }}
                placeholder="Search by name, phone, email, or legacy ID..."
                className="w-full pl-12 pr-4 py-4 border-2 border-green-300 rounded-xl text-lg bg-white focus:ring-4 focus:ring-green-200 outline-none"
              />
            </div>
            {custLoading && <div className="flex items-center gap-2 text-gray-500 py-2"><Loader2 size={16} className="animate-spin" /> Searching...</div>}
            {custResults.length > 0 && (
              <div className="space-y-2 max-h-72 overflow-y-auto">
                {custResults.map((cust, i) => (
                  <button
                    key={cust.id || i}
                    onClick={() => loadCustomer(cust)}
                    className="w-full text-left p-4 bg-white rounded-xl border border-green-200 hover:border-green-500 hover:shadow-md transition-all"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-semibold text-gray-800">{cust.full_name}</span>
                        <span className={`ml-3 text-xs px-2 py-0.5 rounded-full ${cust.status === 'ENROLLED' ? 'bg-green-100 text-green-700' : cust.status === 'SOLD' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600'}`}>{cust.status}</span>
                      </div>
                      <ArrowRight size={16} className="text-green-500" />
                    </div>
                    <div className="text-sm text-gray-500 mt-1 flex gap-4 flex-wrap">
                      {cust.phone && <span className="flex items-center gap-1"><Phone size={12} /> {cust.phone}</span>}
                      {cust.email && <span className="flex items-center gap-1"><Mail size={12} /> {cust.email}</span>}
                      {cust.salesrep && <span className="flex items-center gap-1"><User size={12} /> {cust.salesrep}</span>}
                      {cust.legacy_id && <span className="flex items-center gap-1"><Hash size={12} /> {cust.legacy_id}</span>}
                    </div>
                  </button>
                ))}
              </div>
            )}
            {custSearch.length >= 2 && !custLoading && custResults.length === 0 && (
              <p className="text-sm text-gray-500 py-2">No customers found for "{custSearch}"</p>
            )}
          </div>
        )}
      </Card>

      {/* Buyer Information */}
      <Section title="Buyer Information" icon={User} helpText="Enter the primary buyer's personal details">
        <Row>
          <Field label="First Name" name="buyer_first_name" value={data.buyer_first_name} onChange={c} resetKey={resetKey} half required icon={User} error={errOf('buyer_first_name')} />
          <Field label="Last Name" name="buyer_last_name" value={data.buyer_last_name} onChange={c} resetKey={resetKey} half required icon={User} error={errOf('buyer_last_name')} />
        </Row>
        <Row>
          <Field label="Phone Number" name="buyer_phone" value={data.buyer_phone} onChange={c} resetKey={resetKey} half type="phone" icon={Phone} error={errOf('buyer_phone')} />
          <Field label="Email Address" name="buyer_email" value={data.buyer_email} onChange={c} resetKey={resetKey} half type="email" icon={Mail} error={errOf('buyer_email')} />
        </Row>
        <Row>
          <Field label="Social Security #" name="buyer_ssn" value={data.buyer_ssn} onChange={c} resetKey={resetKey} third type="ssn" icon={Hash} />
          <Field label="Date of Birth" name="buyer_dob" value={data.buyer_dob} onChange={c} resetKey={resetKey} third type="date" icon={Calendar} />
          <Field label="Marital Status" name="buyer_marital_status" value={data.buyer_marital_status} onChange={c} resetKey={resetKey} third />
        </Row>
      </Section>

      {/* Co-Buyer */}
      <Section title="Co-Buyer (Optional)" icon={User} open={false}
        badge={data.co_buyer_first_name ? <Badge color="green">Added</Badge> : null}>
        <Row>
          <Field label="First Name" name="co_buyer_first_name" value={data.co_buyer_first_name} onChange={c} resetKey={resetKey} half icon={User} />
          <Field label="Last Name" name="co_buyer_last_name" value={data.co_buyer_last_name} onChange={c} resetKey={resetKey} half icon={User} />
        </Row>
        <Row>
          <Field label="Phone" name="co_buyer_phone" value={data.co_buyer_phone} onChange={c} resetKey={resetKey} third type="phone" icon={Phone} />
          <Field label="SSN" name="co_buyer_ssn" value={data.co_buyer_ssn} onChange={c} resetKey={resetKey} third type="ssn" icon={Hash} />
          <Field label="Marital Status" name="co_buyer_marital_status" value={data.co_buyer_marital_status} onChange={c} resetKey={resetKey} third />
        </Row>
      </Section>

      {/* Mailing Address */}
      <Section title="Mailing Address" icon={MapPin} open={false}>
        <Row>
          <Field label="Street Address" name="mailing_address" value={data.mailing_address} onChange={c} resetKey={resetKey} icon={MapPinned} />
        </Row>
        <Row>
          <Field label="City" name="mailing_city" value={data.mailing_city} onChange={c} resetKey={resetKey} third />
          <Field label="State" name="mailing_state" value={data.mailing_state} onChange={c} resetKey={resetKey} third />
          <Field label="ZIP Code" name="mailing_zip" value={data.mailing_zip} onChange={c} resetKey={resetKey} third />
        </Row>
      </Section>

      {/* Employment */}
      <Section title="Employment Information" icon={Briefcase} open={false}>
        <Row>
          <Field label="Employer Name" name="employer_name" value={data.employer_name} onChange={c} resetKey={resetKey} half icon={Building2} />
          <Field label="Occupation" name="occupation" value={data.occupation} onChange={c} resetKey={resetKey} half />
        </Row>
        <Row>
          <Field label="Length of Employment" name="occupation_length" value={data.occupation_length} onChange={c} resetKey={resetKey} half />
          <Field label="Work Phone" name="work_phone" value={data.work_phone} onChange={c} resetKey={resetKey} half type="phone" icon={Phone} />
        </Row>
      </Section>

      {/* Save + Next Buttons */}
      <div className="flex justify-between items-center pt-4 gap-4">
        {canProceed && (
          <button
            type="button"
            onClick={saveCurrentCustomer}
            disabled={savingCustomer}
            className="flex items-center gap-2 px-5 py-3 text-sm font-medium text-green-700 bg-green-50 border border-green-300 rounded-xl hover:bg-green-100 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {savingCustomer ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
            {savingCustomer ? 'Saving Customer...' : 'Save Customer'}
          </button>
        )}
        <BigButton
          onClick={onNext}
          icon={ArrowRight}
        >
          Continue to Home Selection
        </BigButton>
      </div>

      {customerSaveState.message && (
        <div className={`rounded-xl p-4 text-sm font-medium ${
          customerSaveState.status === 'saved'
            ? 'bg-green-50 text-green-700 border border-green-200'
            : customerSaveState.status === 'error'
              ? 'bg-red-50 text-red-700 border border-red-200'
              : 'bg-blue-50 text-blue-700 border border-blue-200'
        }`}>
          {customerSaveState.message}
        </div>
      )}

      {!canProceed && (
        <div className="text-center text-amber-700 bg-amber-50 border-2 border-amber-200 rounded-xl p-4">
          <Info size={20} className="inline mr-2" />
          {liveValidation.errors.length > 0
            ? <>Required to continue: <span className="font-bold">{liveValidation.errors.join(' · ')}</span></>
            : <>Please complete the required fields above to continue</>}
        </div>
      )}
    </div>
  );
}

/* ─── Step 2: Choose Home from Inventory ─────────────────── */

function Step2({ data, onChange, resetKey, inventory, inventoryLoading, onNext, onBack, validationErrors, missingFields, onOpenTradeInCalculator, onAutoFill, autoFilledFields }) {
  const [filter, setFilter] = useState('all'); // all, new, used
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedHome, setSelectedHome] = useState(null);

  const c = (n, v) => onChange(n, v);
  const filled = autoFilledFields || new Set();
  const isAutoFilled = (name) => filled.has(name) && hasValue(data[name]);

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
    // Build the patch from inventory (only fields with non-empty values get
    // marked as auto-filled). Serial #, Label #, Sale Price are always null
    // in the texashomeoutlet.com feed, so they fall through to manual entry
    // even if the inventory record nominally exposes the keys.
    const patch = {};
    if (hasValue(home.manufacturer)) patch.manufacturer = String(home.manufacturer);
    if (hasValue(home.model_name)) patch.model = String(home.model_name);
    if (hasValue(home.year)) patch.year = String(home.year);
    if (hasValue(home.serial_number)) patch.serial_number_1 = String(home.serial_number);
    if (hasValue(home.label_number)) patch.label_number_1 = String(home.label_number);
    if (hasValue(home.sections)) patch.no_of_sections = String(home.sections);
    if (home.sale_price && Number(home.sale_price) > 0) {
      patch.sales_price = String(home.sale_price);
    }
    // is_new is always meaningful (boolean toggle, not a Field)
    c('is_new', home.is_new !== false);
    if (onAutoFill) {
      onAutoFill(patch);
    } else {
      // Defensive fallback: still write fields one at a time
      Object.entries(patch).forEach(([k, v]) => c(k, v));
    }
  };

  const handleClearSelection = () => {
    setSelectedHome(null);
    const cleared = {
      manufacturer: '',
      model: '',
      year: '',
      serial_number_1: '',
      serial_number_2: '',
      label_number_1: '',
      label_number_2: '',
      no_of_sections: '',
    };
    if (onAutoFill) {
      onAutoFill(cleared, { clear: true });
    } else {
      Object.entries(cleared).forEach(([k, v]) => c(k, v));
    }
  };

  const liveValidation = getValidationState(data, 2);
  const canProceed = liveValidation.missing.size === 0;
  const missing = missingFields || new Set();
  const errOf = (name) => missing.has(name);

  // Human-readable labels for the inline "what's missing" hint
  const missingLabels = {
    manufacturer: 'Manufacturer',
    model: 'Model',
    serial_number_1: 'Serial # 1',
  };
  const missingHint = Array.from(liveValidation.missing)
    .map(n => missingLabels[n])
    .filter(Boolean);

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

        {/* Trade-In Calculator - Only for Pre-Owned */}
        {!data.is_new && (
          <div className="mb-6 p-4 bg-amber-50 border border-amber-200 rounded-xl">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-amber-100 rounded-lg flex items-center justify-center">
                  <Calculator size={20} className="text-amber-600" />
                </div>
                <div>
                  <h4 className="font-semibold text-amber-800">Trade-In Calculator</h4>
                  <p className="text-sm text-amber-600">Evaluate trade-in value for pre-owned homes</p>
                </div>
              </div>
              <button
                type="button"
                onClick={onOpenTradeInCalculator}
                className="px-4 py-2 bg-amber-600 text-white rounded-lg font-semibold hover:bg-amber-700 flex items-center gap-2"
              >
                <BadgeDollarSign size={18} />
                Open Calculator
              </button>
            </div>
            {data.trade_in_value > 0 && (
              <div className="mt-3 pt-3 border-t border-amber-200 flex items-center justify-between">
                <span className="text-amber-700">Trade-In Value Applied:</span>
                <span className="text-xl font-bold text-amber-800">${parseInt(data.trade_in_value).toLocaleString()}</span>
              </div>
            )}
          </div>
        )}

        <Row>
          <Field label="Manufacturer" name="manufacturer" value={data.manufacturer} onChange={c} resetKey={resetKey} half required icon={Building2} error={errOf('manufacturer')} autoFilled={isAutoFilled('manufacturer')} />
          <Field label="Model Name" name="model" value={data.model} onChange={c} resetKey={resetKey} half required icon={Home} error={errOf('model')} autoFilled={isAutoFilled('model')} />
        </Row>
        <Row>
          <Field label="Year" name="year" value={data.year} onChange={c} resetKey={resetKey} third autoFilled={isAutoFilled('year')} />
          <Field
            label="Serial # 1"
            name="serial_number_1"
            value={data.serial_number_1}
            onChange={c}
            resetKey={resetKey}
            third
            required
            icon={Hash}
            error={errOf('serial_number_1')}
            autoFilled={isAutoFilled('serial_number_1')}
            helperText={selectedHome && !isAutoFilled('serial_number_1') ? 'Enter manually — not in inventory feed' : undefined}
          />
          <Field label="Serial # 2" name="serial_number_2" value={data.serial_number_2} onChange={c} resetKey={resetKey} third icon={Hash} />
        </Row>
        <Row>
          <Field
            label="Label # 1"
            name="label_number_1"
            value={data.label_number_1}
            onChange={c}
            resetKey={resetKey}
            third
            autoFilled={isAutoFilled('label_number_1')}
            helperText={selectedHome && !isAutoFilled('label_number_1') ? 'Enter manually — not in inventory feed' : undefined}
          />
          <Field label="Label # 2" name="label_number_2" value={data.label_number_2} onChange={c} resetKey={resetKey} third />
          <Field label="# of Sections" name="no_of_sections" value={data.no_of_sections} onChange={c} resetKey={resetKey} third autoFilled={isAutoFilled('no_of_sections')} />
        </Row>
      </Card>

      {/* Installation Site */}
      <Section title="Installation Site Address" icon={MapPin}>
        <Row>
          <Field label="Street Address" name="buyer_address" value={data.buyer_address} onChange={c} resetKey={resetKey} required icon={MapPinned} />
        </Row>
        <Row>
          <Field label="City" name="buyer_city" value={data.buyer_city} onChange={c} resetKey={resetKey} third required />
          <Field label="County" name="buyer_county" value={data.buyer_county} onChange={c} resetKey={resetKey} third />
          <Field label="State" name="buyer_state" value={data.buyer_state} onChange={c} resetKey={resetKey} third />
        </Row>
        <Row>
          <Field label="ZIP Code" name="buyer_zip" value={data.buyer_zip} onChange={c} resetKey={resetKey} half />
        </Row>
      </Section>

      {/* Pricing */}
      <Section title="Pricing Information" icon={BadgeDollarSign}>
        <Row>
          <Field
            label="Sales Price"
            name="sales_price"
            value={data.sales_price}
            onChange={c}
            resetKey={resetKey}
            third
            type="currency"
            required
            icon={DollarSign}
            error={errOf('sales_price')}
            autoFilled={isAutoFilled('sales_price')}
            helperText={selectedHome && !isAutoFilled('sales_price') ? 'Enter manually — not in inventory feed' : undefined}
          />
          <Field label="Down Payment" name="down_payment" value={data.down_payment} onChange={c} resetKey={resetKey} third type="currency" icon={DollarSign} />
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
          <Field label="Creditor Name" name="creditor_name" value={data.creditor_name} onChange={c} resetKey={resetKey} half />
          <Field label="Creditor Phone" name="creditor_phone" value={data.creditor_phone} onChange={c} resetKey={resetKey} half type="phone" icon={Phone} />
        </Row>
        <Row>
          <Field label="Loan Term (months)" name="loan_term" value={data.loan_term} onChange={c} resetKey={resetKey} third />
          <Field label="APR (%)" name="apr" value={data.apr} onChange={c} resetKey={resetKey} third />
          <Field label="Monthly Payment" name="monthly_payment" value={data.monthly_payment} onChange={c} resetKey={resetKey} third type="currency" icon={DollarSign} />
        </Row>
        <Row>
          <Field label="Sales Representative" name="salesrep" value={data.salesrep} onChange={c} resetKey={resetKey} half />
          <Field label="Payment Start Date" name="payment_start_date" value={data.payment_start_date} onChange={c} resetKey={resetKey} half type="date" icon={Calendar} />
        </Row>
      </Section>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-6">
        <BigButton variant="secondary" onClick={onBack} icon={ArrowLeft}>
          Back to Customer Info
        </BigButton>
        <BigButton onClick={onNext} icon={ArrowRight}>
          Continue to Documents
        </BigButton>
      </div>

      {!canProceed && (
        <div className="text-center text-amber-700 bg-amber-50 border-2 border-amber-200 rounded-xl p-4 mt-4">
          <Info size={20} className="inline mr-2" />
          {missingHint.length > 0
            ? <>Required to continue: <span className="font-bold">{missingHint.join(', ')}</span></>
            : <>Please complete the required fields above to continue</>}
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

function Step4({ results, generating, error, onBack, onReset, onDownload, downloadingDoc, downloadError }) {
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
        <button
          type="button"
          onClick={() => onDownload(results.merged.download_url, results.merged.filename)}
          disabled={downloadingDoc === results.merged.download_url}
          className="flex items-center justify-center gap-3 w-full py-5 bg-blue-600 text-white rounded-xl font-bold text-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200 disabled:bg-blue-300"
        >
          {downloadingDoc === results.merged.download_url ? <Loader2 size={28} className="animate-spin" /> : <Download size={28} />}
          Download All Documents ({results.merged.page_count} pages)
        </button>
      )}

      {downloadError && (
        <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {downloadError}
        </div>
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
            <button
              type="button"
              onClick={() => onDownload(d.download_url, d.filename)}
              disabled={downloadingDoc === d.download_url}
              className="flex items-center gap-2 px-4 py-2 bg-blue-100 text-blue-700 rounded-lg font-bold hover:bg-blue-200 transition-colors flex-shrink-0 disabled:opacity-60"
            >
              {downloadingDoc === d.download_url ? <Loader2 size={18} className="animate-spin" /> : <Download size={18} />}
              PDF
            </button>
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

/* ─── Trade-In Calculator (Pre-Owned Workflow) ───────────── */

function TradeInCalculator({ onClose, onApply, initialValue = 0 }) {
  const [calculator, setCalculator] = useState({
    year: 2020,
    make: '',
    model: '',
    condition: 'good',
    mileage: 50000,
    baseValue: initialValue || 15000,
    adjustments: {
      exterior: 0,
      interior: 0,
      mechanical: 0,
      tires: 0,
      appliances: 0
    },
    notes: ''
  });

  const conditions = [
    { id: 'excellent', label: 'Excellent', multiplier: 1.1, desc: 'Like new, no repairs needed' },
    { id: 'good', label: 'Good', multiplier: 1.0, desc: 'Minor wear, minor repairs needed' },
    { id: 'fair', label: 'Fair', multiplier: 0.85, desc: 'Visible wear, some repairs needed' },
    { id: 'poor', label: 'Poor', multiplier: 0.7, desc: 'Significant issues, major repairs' }
  ];

  const adjustmentItems = [
    { key: 'exterior', label: 'Exterior Condition', options: [
      { value: 0, label: 'Good - No deduction' },
      { value: -500, label: 'Minor damage - $500' },
      { value: -1000, label: 'Major damage - $1,000' },
      { value: -2000, label: 'Needs replacement - $2,000' }
    ]},
    { key: 'interior', label: 'Interior Condition', options: [
      { value: 0, label: 'Good - No deduction' },
      { value: -300, label: 'Minor wear - $300' },
      { value: -800, label: 'Major wear - $800' },
      { value: -1500, label: 'Needs renovation - $1,500' }
    ]},
    { key: 'mechanical', label: 'Mechanical Systems', options: [
      { value: 0, label: 'All working - No deduction' },
      { value: -500, label: 'Minor issues - $500' },
      { value: -1500, label: 'Major issues - $1,500' },
      { value: -3000, label: 'Non-functional - $3,000' }
    ]},
    { key: 'tires', label: 'Tires & Wheels', options: [
      { value: 0, label: 'Good condition - No deduction' },
      { value: -400, label: 'Need replacement - $400' }
    ]},
    { key: 'appliances', label: 'Appliances', options: [
      { value: 0, label: 'All working - No deduction' },
      { value: -200, label: '1 needs repair - $200' },
      { value: -500, label: 'Multiple issues - $500' },
      { value: -1000, label: 'Need replacement - $1,000' }
    ]}
  ];

  const calculateValue = () => {
    const conditionMultiplier = conditions.find(c => c.id === calculator.condition)?.multiplier || 1;
    const baseAdjusted = calculator.baseValue * conditionMultiplier;
    const totalAdjustments = Object.values(calculator.adjustments).reduce((a, b) => a + b, 0);
    return Math.max(0, Math.round(baseAdjusted + totalAdjustments));
  };

  const tradeInValue = calculateValue();

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-white border-b px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-amber-100 rounded-xl flex items-center justify-center">
              <BadgeDollarSign size={24} className="text-amber-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-800">Trade-In Calculator</h2>
              <p className="text-sm text-gray-500">Evaluate pre-owned home trade-in value</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X size={24} className="text-gray-400" />
          </button>
        </div>

        <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left Column - Home Details */}
          <div className="space-y-6">
            <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
              <h3 className="font-semibold text-blue-800 mb-3 flex items-center gap-2">
                <Home size={18} />
                Home Details
              </h3>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-sm font-medium text-gray-600">Year</label>
                  <input
                    type="number"
                    value={calculator.year}
                    onChange={e => setCalculator(c => ({ ...c, year: parseInt(e.target.value) }))}
                    className="w-full mt-1 px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-600">Base Value ($)</label>
                  <input
                    type="number"
                    value={calculator.baseValue}
                    onChange={e => setCalculator(c => ({ ...c, baseValue: parseInt(e.target.value) }))}
                    className="w-full mt-1 px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
              <div className="mt-3">
                <label className="text-sm font-medium text-gray-600">Manufacturer</label>
                <input
                  type="text"
                  value={calculator.make}
                  onChange={e => setCalculator(c => ({ ...c, make: e.target.value }))}
                  placeholder="e.g., Clayton, Fleetwood"
                  className="w-full mt-1 px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="mt-3">
                <label className="text-sm font-medium text-gray-600">Model</label>
                <input
                  type="text"
                  value={calculator.model}
                  onChange={e => setCalculator(c => ({ ...c, model: e.target.value }))}
                  placeholder="e.g., The Big Steve"
                  className="w-full mt-1 px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>

            <div>
              <h3 className="font-semibold text-gray-800 mb-3">Overall Condition</h3>
              <div className="space-y-2">
                {conditions.map(cond => (
                  <label
                    key={cond.id}
                    className={`flex items-center gap-3 p-3 rounded-xl border-2 cursor-pointer transition-all ${
                      calculator.condition === cond.id
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <input
                      type="radio"
                      name="condition"
                      value={cond.id}
                      checked={calculator.condition === cond.id}
                      onChange={e => setCalculator(c => ({ ...c, condition: e.target.value }))}
                      className="w-4 h-4 text-blue-600"
                    />
                    <div className="flex-1">
                      <div className="font-medium text-gray-800">{cond.label}</div>
                      <div className="text-sm text-gray-500">{cond.desc}</div>
                    </div>
                    <div className="text-sm font-semibold text-blue-600">
                      {cond.multiplier >= 1 ? '+' : ''}{Math.round((cond.multiplier - 1) * 100)}%
                    </div>
                  </label>
                ))}
              </div>
            </div>
          </div>

          {/* Right Column - Adjustments */}
          <div className="space-y-6">
            <div>
              <h3 className="font-semibold text-gray-800 mb-3">Condition Adjustments</h3>
              <div className="space-y-3">
                {adjustmentItems.map(item => (
                  <div key={item.key} className="bg-gray-50 rounded-xl p-3">
                    <label className="text-sm font-medium text-gray-700 mb-2 block">
                      {item.label}
                    </label>
                    <select
                      value={calculator.adjustments[item.key]}
                      onChange={e => setCalculator(c => ({
                        ...c,
                        adjustments: { ...c.adjustments, [item.key]: parseInt(e.target.value) }
                      }))}
                      className="w-full px-3 py-2 border rounded-lg text-sm"
                    >
                      {item.options.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <label className="text-sm font-medium text-gray-700 mb-2 block">Notes</label>
              <textarea
                value={calculator.notes}
                onChange={e => setCalculator(c => ({ ...c, notes: e.target.value }))}
                placeholder="Additional notes about condition, special features, etc."
                rows={4}
                className="w-full px-3 py-2 border rounded-lg resize-none"
              />
            </div>
          </div>
        </div>

        {/* Footer with Value */}
        <div className="sticky bottom-0 bg-gray-50 border-t px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="text-sm text-gray-500">
                <div>Base: ${calculator.baseValue.toLocaleString()}</div>
                <div>Condition: {conditions.find(c => c.id === calculator.condition)?.label}</div>
                <div>Adjustments: ${Object.values(calculator.adjustments).reduce((a,b) => a+b, 0).toLocaleString()}</div>
              </div>
            </div>
            <div className="flex items-center gap-6">
              <div className="text-right">
                <div className="text-sm text-gray-500">Trade-In Value</div>
                <div className="text-3xl font-bold text-green-600">${tradeInValue.toLocaleString()}</div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={onClose}
                  className="px-6 py-3 border-2 border-gray-300 rounded-xl font-semibold text-gray-600 hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  onClick={() => onApply({ value: tradeInValue, details: calculator })}
                  className="px-6 py-3 bg-blue-600 text-white rounded-xl font-semibold hover:bg-blue-700 flex items-center gap-2"
                >
                  <Check size={20} />
                  Apply to Deal
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Form Validation ────────────────────────────────────── */

function getValidationState(form, step) {
  const errors = [];
  const missing = new Set();
  const push = (field, msg) => { errors.push(msg); if (field) missing.add(field); };

  if (step === 1) {
    if (!form.buyer_first_name?.trim()) push('buyer_first_name', 'Buyer first name is required');
    if (!form.buyer_last_name?.trim()) push('buyer_last_name', 'Buyer last name is required');
    if (form.buyer_email && !form.buyer_email.includes('@')) push('buyer_email', 'Invalid email address');
    if (form.buyer_phone && form.buyer_phone.replace(/\D/g, '').length < 10) {
      push('buyer_phone', 'Phone number should be at least 10 digits');
    }
  }

  if (step === 2) {
    if (!form.manufacturer?.trim()) push('manufacturer', 'Manufacturer is required');
    if (!form.model?.trim()) push('model', 'Model name is required');
    if (!form.serial_number_1?.trim()) push('serial_number_1', 'Serial # 1 is required');
    if (form.sales_price && isNaN(parseFloat(form.sales_price))) {
      push('sales_price', 'Sales price must be a number');
    }
  }

  return { errors, missing };
}

function validateForm(form, step) {
  return getValidationState(form, step).errors;
}

/* ─── Main Component ─────────────────────────────────────── */

export default function DocumentCenter() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({ ...INITIAL_FORM });
  const [formResetKey, setFormResetKey] = useState(0); // Increment to force Field re-mount
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
  const [missingFields, setMissingFields] = useState(new Set());
  const [lastSaved, setLastSaved] = useState(null);
  const [showDuplicateWarning, setShowDuplicateWarning] = useState(null);
  const [showTradeInCalculator, setShowTradeInCalculator] = useState(false);
  const [readiness, setReadiness] = useState(null);
  const [docHistory, setDocHistory] = useState([]);
  const [deskLoading, setDeskLoading] = useState(false);
  const [deskError, setDeskError] = useState('');
  const [downloadingDoc, setDownloadingDoc] = useState('');
  const [downloadError, setDownloadError] = useState('');
  const [autoFilledFields, setAutoFilledFields] = useState(new Set());

  // Refs to avoid stale closures in useCallback (prevents re-render on every keystroke)
  const formRef = useRef(form);
  formRef.current = form;
  const dealsRef = useRef(deals);
  dealsRef.current = deals;

  const loadDocumentDesk = useCallback(async () => {
    setDeskLoading(true);
    setDeskError('');
    try {
      const [readinessResponse, historyResponse] = await Promise.all([
        adminFetch('/api/documents/readiness'),
        adminFetch('/api/documents/history'),
      ]);

      if (!readinessResponse.ok) throw new Error('Document readiness check failed');
      if (!historyResponse.ok) throw new Error('Document history failed to load');

      const readinessData = await readinessResponse.json();
      const historyData = await historyResponse.json();
      setReadiness(readinessData);
      setDocHistory(historyData.documents || []);
    } catch (e) {
      setDeskError(e.message || 'Document status unavailable');
    } finally {
      setDeskLoading(false);
    }
  }, []);

  const handleDocumentDownload = useCallback(async (url, filename) => {
    setDownloadError('');
    setDownloadingDoc(url);
    try {
      await downloadAdminFile(url, filename);
    } catch (e) {
      setDownloadError(e.message || 'Download failed');
    } finally {
      setDownloadingDoc('');
    }
  }, []);

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

    loadDocumentDesk();
    
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
  }, [loadDocumentDesk]);

  // Auto-save to localStorage — use ref for lastSaved to avoid re-render
  const lastSavedRef = useRef(lastSaved);
  useEffect(() => {
    const timeout = setTimeout(() => {
      const draft = {
        form,
        selDocs,
        step,
        timestamp: new Date().toISOString()
      };
      localStorage.setItem('document_center_draft', JSON.stringify(draft));
      // Use ref to avoid triggering a re-render during typing
      const now = new Date();
      lastSavedRef.current = now;
      // Only update state (causing re-render) if NOT actively typing
      // by debouncing the state update further
      setTimeout(() => setLastSaved(now), 1000);
    }, 5000); // Save 5 seconds after last change (was 3s)

    return () => clearTimeout(timeout);
  }, [form, selDocs, step]);

  // Debounce timer ref for duplicate check
  const dupCheckTimer = useRef(null);

  // Check for duplicate deals (reads deals from ref for stability)
  const checkForDuplicates = useCallback((firstName, lastName, phone) => {
    if (!firstName && !lastName && !phone) {
      setShowDuplicateWarning(null);
      return;
    }

    const currentDeals = dealsRef.current;
    const matches = currentDeals.filter(d => {
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
  }, []);

  const chg = useCallback((n, v) => {
    setForm(p => ({ ...p, [n]: v }));
    setValidationErrors([]); // Clear errors on change
    setMissingFields(prev => {
      if (!prev.has(n)) return prev;
      const next = new Set(prev);
      next.delete(n);
      return next;
    });
    // Once the user manually edits an auto-filled field, drop the badge —
    // they own the value now.
    setAutoFilledFields(prev => {
      if (!prev.has(n)) return prev;
      const next = new Set(prev);
      next.delete(n);
      return next;
    });

    // Debounce duplicate check — only run 500ms after user stops typing
    // This prevents re-renders on every keystroke
    if (n === 'buyer_first_name' || n === 'buyer_last_name' || n === 'buyer_phone') {
      if (dupCheckTimer.current) clearTimeout(dupCheckTimer.current);
      dupCheckTimer.current = setTimeout(() => {
        const currentForm = formRef.current;
        const firstName = n === 'buyer_first_name' ? v : currentForm.buyer_first_name;
        const lastName = n === 'buyer_last_name' ? v : currentForm.buyer_last_name;
        const phone = n === 'buyer_phone' ? v : currentForm.buyer_phone;
        checkForDuplicates(firstName, lastName, phone);
      }, 500);
    }
  }, [checkForDuplicates]); // stable — no dependencies that change on every keystroke
  
  // Clear draft
  const clearDraft = () => {
    localStorage.removeItem('document_center_draft');
    setForm({ ...INITIAL_FORM });
    setSelDocs([]);
    setStep(1);
    setLastSaved(null);
    setAutoFilledFields(new Set());
    setFormResetKey(k => k + 1); // Force Fields to clear
  };

  const loadDeal = useCallback(d => {
    const m = { ...INITIAL_FORM };
    Object.keys(INITIAL_FORM).forEach(k => {
      if (d[k] != null) m[k] = d[k];
    });
    setForm(m);
    setAutoFilledFields(new Set()); // Clear auto-fill markers when loading a deal
    setFormResetKey(k => k + 1); // Force all Fields to re-mount with new defaultValues
  }, []);

  // Inventory auto-fill: applies a patch in one go, bumps resetKey so the
  // uncontrolled <Field> inputs re-mount with the new defaultValue, and
  // tracks which keys came from inventory so the Field can render a
  // "✓ from inventory" badge.
  const applyInventoryAutoFill = useCallback((patch, opts = {}) => {
    setForm(p => ({ ...p, ...patch }));
    setMissingFields(prev => {
      const next = new Set(prev);
      Object.entries(patch).forEach(([k, v]) => {
        if (v !== undefined && v !== null && String(v).trim() !== '') {
          next.delete(k);
        }
      });
      return next;
    });
    if (opts.clear) {
      setAutoFilledFields(new Set());
    } else {
      // Only mark fields that received a non-empty value
      const filled = new Set();
      Object.entries(patch).forEach(([k, v]) => {
        if (v !== undefined && v !== null && String(v).trim() !== '') {
          filled.add(k);
        }
      });
      setAutoFilledFields(filled);
    }
    setFormResetKey(k => k + 1); // Force Fields to re-mount with the new defaultValues
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
    // Defense-in-depth: Step 3's Generate must not advance to backend if
    // Step 1 or Step 2 is missing data. Backend would reject with cryptic
    // "Generation failed" — instead, jump the user back to the offending
    // step with the same inline-highlight / scroll-to-error UX.
    const s1 = getValidationState(form, 1);
    if (s1.errors.length > 0) {
      setValidationErrors(s1.errors);
      setMissingFields(s1.missing);
      setStep(1);
      if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    const s2 = getValidationState(form, 2);
    if (s2.errors.length > 0) {
      setValidationErrors(s2.errors);
      setMissingFields(s2.missing);
      setStep(2);
      if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    if (!selDocs || selDocs.length === 0) {
      setGenErr('Select at least one document or packet to generate.');
      return;
    }

    setGenerating(true);
    setGenErr('');
    setDownloadError('');
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
      loadDocumentDesk();
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
    setDownloadError('');
    setAutoFilledFields(new Set());
    setFormResetKey(k => k + 1);
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

      <DocumentDesk
        readiness={readiness}
        history={docHistory}
        loading={deskLoading}
        error={deskError}
        downloadingDoc={downloadingDoc}
        downloadError={downloadError}
        onRefresh={loadDocumentDesk}
        onDownload={handleDocumentDownload}
      />

      {/* Step Bar */}
      <StepBar step={step} />

      {/* Steps */}
      {step === 1 && (
        <Step1
          data={form}
          onChange={chg}
          resetKey={formResetKey}
          deals={deals}
          dealsLoading={dealsLoading}
          onLoadDeal={loadDeal}
          onNext={() => {
            const { errors, missing } = getValidationState(form, 1);
            if (errors.length > 0) {
              setValidationErrors(errors);
              setMissingFields(missing);
              if (typeof window !== 'undefined') {
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }
            } else {
              setValidationErrors([]);
              setMissingFields(new Set());
              setStep(2);
            }
          }}
          validationErrors={validationErrors}
          missingFields={missingFields}
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
          resetKey={formResetKey}
          inventory={inventory}
          inventoryLoading={inventoryLoading}
          onNext={() => {
            const { errors, missing } = getValidationState(form, 2);
            if (errors.length > 0) {
              setValidationErrors(errors);
              setMissingFields(missing);
              if (typeof window !== 'undefined') {
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }
            } else {
              setValidationErrors([]);
              setMissingFields(new Set());
              setStep(3);
            }
          }}
          onBack={() => setStep(1)}
          validationErrors={validationErrors}
          missingFields={missingFields}
          onOpenTradeInCalculator={() => setShowTradeInCalculator(true)}
          onAutoFill={applyInventoryAutoFill}
          autoFilledFields={autoFilledFields}
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
          onDownload={handleDocumentDownload}
          downloadingDoc={downloadingDoc}
          downloadError={downloadError}
        />
      )}

      {/* Trade-In Calculator Modal */}
      {showTradeInCalculator && (
        <TradeInCalculator
          onClose={() => setShowTradeInCalculator(false)}
          onApply={({ value, details }) => {
            setForm(p => ({
              ...p,
              trade_in_value: value,
              trade_in_details: details,
              // Also update down payment if trade-in is applied
              down_payment: (parseInt(p.down_payment) || 0) + value
            }));
            setShowTradeInCalculator(false);
          }}
          initialValue={form.trade_in_value || 0}
        />
      )}
    </div>
  );
}
