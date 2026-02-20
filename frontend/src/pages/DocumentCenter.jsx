import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText, Download, CheckCircle, AlertCircle, Search,
  Loader2, ChevronRight, ChevronDown,
  Package, User, Home, DollarSign, Briefcase, MapPin,
  ArrowRight, ArrowLeft, RotateCcw, Check, FileCheck,
} from 'lucide-react';
import adminFetch from '../adminFetch';

/* ─── Constants ──────────────────────────────────────────── */

const STEPS = ['Customer Info', 'Select Documents', 'Generate'];

const INITIAL_FORM = {
  salesrep: '',
  buyer_first_name: '', buyer_last_name: '', buyer_phone: '', buyer_email: '',
  buyer_ssn: '', buyer_dob: '', buyer_marital_status: '',
  co_buyer_first_name: '', co_buyer_last_name: '', co_buyer_phone: '',
  co_buyer_ssn: '', co_buyer_dob: '', co_buyer_marital_status: '',
  mailing_address: '', mailing_city: '', mailing_state: 'TX', mailing_zip: '',
  employer_name: '', occupation: '', occupation_length: '', work_phone: '',
  is_new: true, manufacturer: '', model: '', year: '',
  serial_number_1: '', serial_number_2: '',
  label_number_1: '', label_number_2: '', no_of_sections: '',
  buyer_address: '', buyer_city: '', buyer_county: '', buyer_state: 'TX', buyer_zip: '',
  sales_price: '', down_payment: '',
  creditor_name: '', creditor_phone: '', creditor_address: '', creditor_city_state_zip: '',
  loan_term: '', apr: '', finance_charge: '', max_financed: '',
  monthly_payment: '', total_payments: '', payment_start_date: '', insurance_premium: '',
};

const CAT_COLORS = {
  TMHA: 'bg-blue-50 text-blue-700 border-blue-200',
  TDHCA: 'bg-green-50 text-green-700 border-green-200',
  State: 'bg-amber-50 text-amber-700 border-amber-200',
  Internal: 'bg-purple-50 text-purple-700 border-purple-200',
};
const CAT_ORDER = ['TMHA', 'TDHCA', 'State', 'Internal'];

function toDocumentData(f) {
  const buyer = `${f.buyer_first_name} ${f.buyer_last_name}`.trim();
  const coBuyer = `${f.co_buyer_first_name} ${f.co_buyer_last_name}`.trim();
  return { ...f, buyer_name: buyer || undefined, co_buyer_name: coBuyer || undefined,
    buyer_city_state_zip: f.buyer_city ? `${f.buyer_city}, ${f.buyer_state||'TX'} ${f.buyer_zip}`.trim() : undefined };
}

/* ─── Reusable pieces ────────────────────────────────────── */

function StepBar({ step }) {
  return (
    <div className="flex items-center justify-center gap-1 sm:gap-2 mb-6">
      {STEPS.map((label, i) => {
        const n = i + 1, active = step === n, done = step > n;
        return (
          <React.Fragment key={label}>
            {i > 0 && <div className={`h-px w-6 sm:w-12 ${done ? 'bg-blue-500' : 'bg-gray-200'}`} />}
            <div className="flex items-center gap-1.5 sm:gap-2">
              <div className={`w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-xs sm:text-sm font-bold ${
                done ? 'bg-blue-600 text-white' : active ? 'bg-blue-600 text-white ring-4 ring-blue-100' : 'bg-gray-200 text-gray-500'}`}>
                {done ? <Check size={14}/> : n}
              </div>
              <span className={`hidden sm:inline text-sm font-medium ${active?'text-blue-700':done?'text-blue-600':'text-gray-400'}`}>{label}</span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

function Section({ title, icon: Icon, children, open: initOpen = true, badge }) {
  const [open, setOpen] = useState(initOpen);
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <button type="button" onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-gray-50 transition-colors">
        <div className="flex items-center gap-2.5">
          {Icon && <Icon size={18} className="text-blue-600"/>}
          <span className="font-semibold text-gray-800 text-sm">{title}</span>
          {badge}
        </div>
        {open ? <ChevronDown size={18} className="text-gray-400"/> : <ChevronRight size={18} className="text-gray-400"/>}
      </button>
      {open && <div className="px-5 pb-5 pt-1 border-t border-gray-100">{children}</div>}
    </div>
  );
}

function F({ label, name, value, onChange, type='text', placeholder, half, third, required, readOnly }) {
  const cls = `w-full px-3 py-2 border rounded-lg text-sm transition-colors focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none ${
    readOnly ? 'bg-gray-50 text-gray-500 border-gray-200' : 'bg-white border-gray-300 hover:border-gray-400'}`;
  const w = third ? 'w-full sm:w-1/3' : half ? 'w-full sm:w-1/2' : 'w-full';
  return (
    <div className={`${w} px-1.5 mb-3`}>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}{required && <span className="text-red-400 ml-0.5">*</span>}</label>
      <input type={type==='ssn'?'password':type==='currency'?'text':type}
        inputMode={type==='currency'?'decimal':type==='phone'?'tel':undefined}
        name={name} value={value||''} onChange={e=>onChange(name,e.target.value)}
        placeholder={placeholder} readOnly={readOnly} className={cls}/>
    </div>
  );
}

function Row({ children }) { return <div className="flex flex-wrap -mx-1.5">{children}</div>; }

/* ─── Step 1 ─────────────────────────────────────────────── */

function Step1({ data, onChange, deals, dealsLoading, onLoadDeal }) {
  const [q, setQ] = useState('');
  const [showPicker, setShowPicker] = useState(false);
  const filtered = (deals||[]).filter(d => {
    if (!q) return true;
    const s = q.toLowerCase();
    return `${d.buyer_first_name||''} ${d.buyer_last_name||''}`.toLowerCase().includes(s)
      || (d.model||'').toLowerCase().includes(s) || (d.id||'').toLowerCase().includes(s);
  }).slice(0, 8);

  const c = (n, v) => onChange(n, v);
  const sp = parseFloat(String(data.sales_price||'0').replace(/[,$]/g,''))||0;
  const dp = parseFloat(String(data.down_payment||'0').replace(/[,$]/g,''))||0;
  const ub = sp > 0 ? (sp - dp).toFixed(2) : '';

  return (
    <div className="space-y-4">
      {/* Deal loader */}
      <div className="bg-blue-50 rounded-xl border border-blue-200 p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-blue-800">Load from existing deal</span>
          <button type="button" onClick={()=>setShowPicker(!showPicker)} className="text-xs text-blue-600 hover:text-blue-800 font-medium">
            {showPicker ? 'Hide' : 'Search Deals'}
          </button>
        </div>
        {showPicker && (<div>
          <div className="relative mb-2">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"/>
            <input type="text" value={q} onChange={e=>setQ(e.target.value)} placeholder="Search by buyer name, model, or ID..."
              className="w-full pl-8 pr-3 py-2 border border-blue-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none"/>
          </div>
          {dealsLoading ? <div className="flex items-center gap-2 text-xs text-blue-600 py-2"><Loader2 size={14} className="animate-spin"/>Loading...</div>
           : filtered.length > 0 ? (
            <div className="max-h-48 overflow-y-auto space-y-1">{filtered.map(d=>(
              <button key={d.id} type="button" onClick={()=>{onLoadDeal(d);setShowPicker(false);setQ('');}}
                className="w-full text-left px-3 py-2 rounded-lg hover:bg-blue-100 transition-colors text-sm">
                <span className="font-medium text-gray-800">{d.buyer_first_name} {d.buyer_last_name}</span>
                {d.model && <span className="text-gray-500 ml-2">— {d.model}</span>}
                <span className="text-gray-400 text-xs ml-2">#{d.id?.slice(-6)}</span>
              </button>))}</div>
          ) : <p className="text-xs text-blue-500 py-1">No deals found.</p>}
        </div>)}
      </div>

      <Section title="Buyer Information" icon={User}>
        <Row><F label="First Name" name="buyer_first_name" value={data.buyer_first_name} onChange={c} half required/>
             <F label="Last Name" name="buyer_last_name" value={data.buyer_last_name} onChange={c} half required/></Row>
        <Row><F label="Phone" name="buyer_phone" value={data.buyer_phone} onChange={c} half type="phone"/>
             <F label="Email" name="buyer_email" value={data.buyer_email} onChange={c} half type="email"/></Row>
        <Row><F label="SSN" name="buyer_ssn" value={data.buyer_ssn} onChange={c} third type="ssn"/>
             <F label="Date of Birth" name="buyer_dob" value={data.buyer_dob} onChange={c} third type="date"/>
             <F label="Marital Status" name="buyer_marital_status" value={data.buyer_marital_status} onChange={c} third/></Row>
      </Section>

      <Section title="Co-Buyer" icon={User} open={false}
        badge={data.co_buyer_first_name ? <span className="text-xs bg-blue-100 text-blue-600 px-2 py-0.5 rounded-full">Filled</span> : null}>
        <Row><F label="First Name" name="co_buyer_first_name" value={data.co_buyer_first_name} onChange={c} half/>
             <F label="Last Name" name="co_buyer_last_name" value={data.co_buyer_last_name} onChange={c} half/></Row>
        <Row><F label="Phone" name="co_buyer_phone" value={data.co_buyer_phone} onChange={c} third type="phone"/>
             <F label="SSN" name="co_buyer_ssn" value={data.co_buyer_ssn} onChange={c} third type="ssn"/>
             <F label="Marital Status" name="co_buyer_marital_status" value={data.co_buyer_marital_status} onChange={c} third/></Row>
      </Section>

      <Section title="Mailing Address" icon={MapPin} open={false}>
        <Row><F label="Address" name="mailing_address" value={data.mailing_address} onChange={c}/></Row>
        <Row><F label="City" name="mailing_city" value={data.mailing_city} onChange={c} third/>
             <F label="State" name="mailing_state" value={data.mailing_state} onChange={c} third/>
             <F label="ZIP" name="mailing_zip" value={data.mailing_zip} onChange={c} third/></Row>
      </Section>

      <Section title="Employment" icon={Briefcase} open={false}>
        <Row><F label="Employer" name="employer_name" value={data.employer_name} onChange={c} half/>
             <F label="Occupation" name="occupation" value={data.occupation} onChange={c} half/></Row>
        <Row><F label="Length" name="occupation_length" value={data.occupation_length} onChange={c} half/>
             <F label="Work Phone" name="work_phone" value={data.work_phone} onChange={c} half type="phone"/></Row>
      </Section>

      <Section title="Home Details" icon={Home}>
        <div className="flex gap-2 mb-3">
          <button type="button" onClick={()=>onChange('is_new',true)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${data.is_new?'bg-blue-600 text-white':'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>New Home</button>
          <button type="button" onClick={()=>onChange('is_new',false)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${!data.is_new?'bg-blue-600 text-white':'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>Pre-Owned</button>
        </div>
        <Row><F label="Manufacturer" name="manufacturer" value={data.manufacturer} onChange={c} half required/>
             <F label="Model" name="model" value={data.model} onChange={c} half required/></Row>
        <Row><F label="Year" name="year" value={data.year} onChange={c} third/>
             <F label="Serial # 1" name="serial_number_1" value={data.serial_number_1} onChange={c} third required/>
             <F label="Serial # 2" name="serial_number_2" value={data.serial_number_2} onChange={c} third/></Row>
        <Row><F label="Label # 1" name="label_number_1" value={data.label_number_1} onChange={c} third/>
             <F label="Label # 2" name="label_number_2" value={data.label_number_2} onChange={c} third/>
             <F label="Sections" name="no_of_sections" value={data.no_of_sections} onChange={c} third/></Row>
      </Section>

      <Section title="Installation Site" icon={MapPin}>
        <Row><F label="Address" name="buyer_address" value={data.buyer_address} onChange={c} required/></Row>
        <Row><F label="City" name="buyer_city" value={data.buyer_city} onChange={c} third required/>
             <F label="County" name="buyer_county" value={data.buyer_county} onChange={c} third/>
             <F label="State" name="buyer_state" value={data.buyer_state} onChange={c} third/></Row>
        <Row><F label="ZIP" name="buyer_zip" value={data.buyer_zip} onChange={c} half/></Row>
      </Section>

      <Section title="Pricing" icon={DollarSign}>
        <Row><F label="Sales Price" name="sales_price" value={data.sales_price} onChange={c} third type="currency" required placeholder="0.00"/>
             <F label="Down Payment" name="down_payment" value={data.down_payment} onChange={c} third type="currency" placeholder="0.00"/>
             <F label="Unpaid Balance" name="_ub" value={ub} onChange={()=>{}} third readOnly/></Row>
      </Section>

      <Section title="Financing Details" icon={DollarSign} open={false}>
        <Row><F label="Creditor" name="creditor_name" value={data.creditor_name} onChange={c} half/>
             <F label="Creditor Phone" name="creditor_phone" value={data.creditor_phone} onChange={c} half type="phone"/></Row>
        <Row><F label="Creditor Address" name="creditor_address" value={data.creditor_address} onChange={c} half/>
             <F label="City/State/ZIP" name="creditor_city_state_zip" value={data.creditor_city_state_zip} onChange={c} half/></Row>
        <Row><F label="Loan Term (mo)" name="loan_term" value={data.loan_term} onChange={c} third/>
             <F label="APR (%)" name="apr" value={data.apr} onChange={c} third/>
             <F label="Finance Charge" name="finance_charge" value={data.finance_charge} onChange={c} third type="currency"/></Row>
        <Row><F label="Monthly Payment" name="monthly_payment" value={data.monthly_payment} onChange={c} third type="currency"/>
             <F label="Total Payments" name="total_payments" value={data.total_payments} onChange={c} third type="currency"/>
             <F label="Start Date" name="payment_start_date" value={data.payment_start_date} onChange={c} third type="date"/></Row>
        <Row><F label="Sales Rep" name="salesrep" value={data.salesrep} onChange={c} half/>
             <F label="Insurance Premium" name="insurance_premium" value={data.insurance_premium} onChange={c} half type="currency"/></Row>
      </Section>
    </div>
  );
}

/* ─── Step 2 ─────────────────────────────────────────────── */

function Step2({ templates, packets, selected, onToggle, onSelectPacket, isNew }) {
  const [expanded, setExpanded] = useState({ TMHA: true, TDHCA: true, State: false, Internal: false });
  const sorted = [...(packets||[])].sort((a,b) => {
    const ar = isNew ? (a.packet_name.includes('new')||a.packet_name==='standard_closing') : a.packet_name.includes('used');
    const br = isNew ? (b.packet_name.includes('new')||b.packet_name==='standard_closing') : b.packet_name.includes('used');
    return ar===br ? 0 : ar ? -1 : 1;
  });
  const byCat = {};
  (templates||[]).forEach(t => { const c = t.category||'Other'; (byCat[c]=byCat[c]||[]).push(t); });
  const sel = new Set(selected);

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Package size={16} className="text-blue-600"/>Quick Select — Closing Packets</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {sorted.map(p => {
            const rec = isNew ? (p.packet_name==='standard_closing'||p.packet_name==='full_closing_new')
                               : (p.packet_name==='used_home_closing'||p.packet_name==='full_closing_used');
            const cnt = (p.templates||[]).length;
            const ps = (p.templates||[]).filter(t=>sel.has(t)).length;
            const full = ps===cnt && cnt>0;
            return (
              <button key={p.packet_name} type="button" onClick={()=>onSelectPacket(p)}
                className={`relative text-left p-4 rounded-xl border-2 transition-all ${full?'border-blue-500 bg-blue-50 shadow-sm':'border-gray-200 bg-white hover:border-blue-300 hover:shadow-sm'}`}>
                {rec && <span className="absolute -top-2 right-3 bg-blue-600 text-white text-[10px] font-bold px-2 py-0.5 rounded-full">Recommended</span>}
                <div className="font-semibold text-gray-800 text-sm mb-1">{p.display_name}</div>
                <div className="text-xs text-gray-500">{cnt} documents</div>
                {ps>0 && !full && <div className="text-xs text-blue-600 mt-1">{ps}/{cnt} selected</div>}
                {full && <CheckCircle size={16} className="absolute top-3 right-3 text-blue-600"/>}
              </button>);
          })}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-gray-200"/><span className="text-xs text-gray-400 font-medium">or choose individual documents</span><div className="h-px flex-1 bg-gray-200"/>
      </div>

      {CAT_ORDER.map(cat => {
        const docs = byCat[cat]; if (!docs?.length) return null;
        const cs = docs.filter(d=>sel.has(d.template_name)).length;
        const ex = expanded[cat]??false;
        return (
          <div key={cat} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <button type="button" onClick={()=>setExpanded(p=>({...p,[cat]:!p[cat]}))}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors">
              <div className="flex items-center gap-2">
                <span className={`text-xs font-bold px-2 py-0.5 rounded border ${CAT_COLORS[cat]||''}`}>{cat}</span>
                <span className="text-sm font-medium text-gray-700">{docs.length} documents</span>
                {cs>0 && <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">{cs} selected</span>}
              </div>
              {ex ? <ChevronDown size={16} className="text-gray-400"/> : <ChevronRight size={16} className="text-gray-400"/>}
            </button>
            {ex && <div className="border-t border-gray-100 divide-y divide-gray-50">
              {docs.map(doc => {
                const ck = sel.has(doc.template_name);
                return (
                  <label key={doc.template_name} className={`flex items-center px-4 py-2.5 cursor-pointer transition-colors ${ck?'bg-blue-50/50':'hover:bg-gray-50'}`}>
                    <input type="checkbox" checked={ck} onChange={()=>onToggle(doc.template_name)}
                      className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 mr-3 flex-shrink-0"/>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-gray-800 truncate">{doc.display_name}</div>
                      {doc.description && <div className="text-xs text-gray-400 truncate">{doc.description}</div>}
                    </div>
                  </label>);
              })}
            </div>}
          </div>);
      })}

      <div className="bg-gray-50 rounded-xl border border-gray-200 px-4 py-3 flex items-center justify-between">
        <span className="text-sm text-gray-600"><span className="font-bold text-blue-700">{selected.length}</span> document{selected.length!==1?'s':''} selected</span>
        {selected.length>0 && <button type="button" onClick={()=>selected.forEach(s=>onToggle(s))} className="text-xs text-red-500 hover:text-red-700 font-medium">Clear All</button>}
      </div>
    </div>
  );
}

/* ─── Step 3 ─────────────────────────────────────────────── */

function Step3({ results, generating, error }) {
  if (generating) return (
    <div className="flex flex-col items-center justify-center py-16">
      <Loader2 size={40} className="animate-spin text-blue-600 mb-4"/>
      <div className="text-lg font-semibold text-gray-800 mb-2">Generating Documents...</div>
      <div className="text-sm text-gray-500">This may take a moment for large packets.</div>
    </div>);
  if (error) return (
    <div className="flex flex-col items-center justify-center py-16">
      <AlertCircle size={40} className="text-red-400 mb-4"/>
      <div className="text-lg font-semibold text-gray-800 mb-2">Generation Failed</div>
      <div className="text-sm text-red-500">{error}</div>
    </div>);
  if (!results) return null;
  const ok = (results.documents||[]).filter(d=>d.success);
  const fail = (results.documents||[]).filter(d=>!d.success);
  return (
    <div className="space-y-5">
      <div className="bg-green-50 border border-green-200 rounded-xl p-4 flex items-center gap-3">
        <CheckCircle size={24} className="text-green-600 flex-shrink-0"/>
        <div>
          <div className="font-semibold text-green-800">{results.successful} of {results.total} documents generated</div>
          {results.merged && <div className="text-sm text-green-600 mt-0.5">Merged PDF — {results.merged.page_count} pages</div>}
        </div>
      </div>
      {results.merged && (
        <a href={results.merged.download_url} download
          className="flex items-center justify-center gap-2 w-full py-3 bg-blue-600 text-white rounded-xl font-semibold hover:bg-blue-700 transition-colors shadow-sm">
          <Download size={18}/>Download All ({results.merged.page_count} pages, {results.merged.document_count} docs)
        </a>)}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-700">Individual Documents</h3>
        {ok.map(d=>(
          <div key={d.template_name} className="flex items-center justify-between bg-white border border-gray-200 rounded-lg px-4 py-2.5">
            <div className="flex items-center gap-2 min-w-0"><FileCheck size={16} className="text-green-500 flex-shrink-0"/><span className="text-sm text-gray-800 truncate">{d.display_name}</span></div>
            <a href={d.download_url} download className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800 flex-shrink-0 ml-2"><Download size={14}/>PDF</a>
          </div>))}
        {fail.length>0 && <>
          <h4 className="text-xs font-medium text-red-500 mt-3">Skipped ({fail.length})</h4>
          {fail.map(d=>(
            <div key={d.template_name} className="flex items-center gap-2 bg-red-50 border border-red-100 rounded-lg px-4 py-2 text-sm text-red-600">
              <AlertCircle size={14} className="flex-shrink-0"/><span className="truncate">{d.display_name}</span>
              <span className="text-xs text-red-400 ml-auto flex-shrink-0 max-w-[200px] truncate">{d.message}</span>
            </div>))}
        </>}
      </div>
    </div>);
}

/* ─── Main ───────────────────────────────────────────────── */

export default function DocumentCenter() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({...INITIAL_FORM});
  const [selDocs, setSelDocs] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [packets, setPackets] = useState([]);
  const [deals, setDeals] = useState([]);
  const [dealsLoading, setDealsLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [results, setResults] = useState(null);
  const [genErr, setGenErr] = useState('');

  useEffect(() => {
    adminFetch('/api/documents/templates').then(r=>r.json()).then(d=>{setTemplates(d.templates||[]);setPackets(d.packets||[]);}).catch(()=>{});
  }, []);
  useEffect(() => {
    setDealsLoading(true);
    adminFetch('/api/deals?limit=100').then(r=>r.json()).then(d=>setDeals(d.deals||[])).catch(()=>{}).finally(()=>setDealsLoading(false));
  }, []);

  const chg = useCallback((n,v) => setForm(p=>({...p,[n]:v})), []);
  const loadDeal = useCallback(d => {
    const m = {...INITIAL_FORM};
    Object.keys(INITIAL_FORM).forEach(k=>{ if(d[k]!=null) m[k]=d[k]; });
    setForm(m);
  }, []);
  const toggleDoc = useCallback(t => setSelDocs(p=>p.includes(t)?p.filter(x=>x!==t):[...p,t]), []);
  const selectPacket = useCallback(pk => {
    const tpls = pk.templates||[];
    setSelDocs(p => {
      const s = new Set(p);
      if (tpls.every(t=>s.has(t))) return p.filter(t=>!tpls.includes(t));
      tpls.forEach(t=>s.add(t)); return [...s];
    });
  }, []);

  const generate = async () => {
    setGenerating(true); setGenErr(''); setResults(null); setStep(3);
    try {
      const r = await adminFetch('/api/documents/generate-batch', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ templates:selDocs, data:toDocumentData(form), merge:true }),
      });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      setResults(d);
    } catch(e) { setGenErr(e.message||'Generation failed'); }
    finally { setGenerating(false); }
  };

  const reset = () => { setStep(1); setForm({...INITIAL_FORM}); setSelDocs([]); setResults(null); setGenErr(''); };

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2.5"><FileText size={24} className="text-blue-600"/>Document Center</h1>
        <p className="text-sm text-gray-500 mt-1">Enter customer info once — generate all the documents you need.</p>
      </div>
      <StepBar step={step}/>

      {step===1 && <>
        <Step1 data={form} onChange={chg} deals={deals} dealsLoading={dealsLoading} onLoadDeal={loadDeal}/>
        <div className="flex justify-end mt-6">
          <button onClick={()=>setStep(2)} disabled={!form.buyer_first_name && !form.manufacturer}
            className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl font-medium hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-sm">
            Next: Select Documents <ArrowRight size={16}/></button>
        </div>
      </>}

      {step===2 && <>
        <Step2 templates={templates} packets={packets} selected={selDocs} onToggle={toggleDoc} onSelectPacket={selectPacket} isNew={form.is_new}/>
        <div className="flex items-center justify-between mt-6">
          <button onClick={()=>setStep(1)} className="flex items-center gap-2 px-5 py-2.5 text-gray-600 bg-white border border-gray-300 rounded-xl font-medium hover:bg-gray-50 transition-colors">
            <ArrowLeft size={16}/>Back</button>
          <button onClick={generate} disabled={!selDocs.length}
            className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl font-medium hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-sm">
            <FileText size={16}/>Generate {selDocs.length} Document{selDocs.length!==1?'s':''}</button>
        </div>
      </>}

      {step===3 && <>
        <Step3 results={results} generating={generating} error={genErr}/>
        {!generating && <div className="flex items-center justify-between mt-6">
          <button onClick={()=>setStep(2)} className="flex items-center gap-2 px-5 py-2.5 text-gray-600 bg-white border border-gray-300 rounded-xl font-medium hover:bg-gray-50 transition-colors">
            <ArrowLeft size={16}/>Back to Selection</button>
          <button onClick={reset} className="flex items-center gap-2 px-5 py-2.5 text-gray-600 bg-white border border-gray-300 rounded-xl font-medium hover:bg-gray-50 transition-colors">
            <RotateCcw size={16}/>Start New</button>
        </div>}
      </>}
    </div>
  );
}
