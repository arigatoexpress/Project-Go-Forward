import React, { useState, useEffect, useCallback } from 'react';
import { ClipboardList, Plus, Pencil, EyeOff, Undo2, Loader2, X, Camera, AlertTriangle } from 'lucide-react';
import adminFetch from '../adminFetch';
import ConfirmDialog from '../components/ConfirmDialog';
import { extractErrorMessage, safeUserMessage } from '../utils/apiError';
import {
  friendlyModelName, possibleDuplicateIds, staffVisibleHomes, statusLabel, stockLabel,
} from '../utils/inventoryDisplay';

// Staff Inventory Manager — create / edit / retire homes in the in-app
// (Firestore) inventory store the public site serves when INVENTORY_SOURCE=
// firestore. Wired to the admin CRUD endpoints (POST/PUT/DELETE /api/inventory).
// Cost fields are never shown or sent — the public read path hides dealer cost.

const STATUSES = ['AVAILABLE', 'PENDING', 'RESERVED', 'SOLD', 'RETIRED'];
const CLASSIFICATIONS = ['Single Wide', 'Double Wide'];

const EMPTY = {
  model_name: '', manufacturer: '', classification: 'Single Wide', status: 'AVAILABLE',
  serial_number: '', bedrooms: '', bathrooms: '', sqft: '', width: '', length: '',
  is_new: true, features: '', sale_price: '',
};

function toPayload(form) {
  const num = (v) => (v === '' || v === null ? undefined : Number(v));
  const payload = {
    model_name: form.model_name.trim(),
    manufacturer: form.manufacturer.trim() || undefined,
    classification: form.classification || undefined,
    status: form.status || undefined,
    serial_number: form.serial_number.trim() || undefined,
    bedrooms: num(form.bedrooms), bathrooms: num(form.bathrooms), sqft: num(form.sqft),
    width: num(form.width), length: num(form.length), is_new: !!form.is_new,
    sale_price: Number(form.sale_price || 0),
    features: form.features ? form.features.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
  };
  Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
  return payload;
}

export default function InventoryManager({ onBack, onNavigate }) {
  const [homes, setHomes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState(null); // {type, text}
  const [editing, setEditing] = useState(null); // null | 'new' | home id
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [confirmRetire, setConfirmRetire] = useState(null); // home pending removal
  const [retiring, setRetiring] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await adminFetch('/api/inventory?status=&limit=500');
      const data = await res.json();
      if (data.success) setHomes(data.inventory || []);
      else setError(safeUserMessage(extractErrorMessage(data), 'Failed to load inventory.'));
    } catch {
      setError('Failed to load inventory.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const startNew = () => { setForm(EMPTY); setEditing('new'); setMessage(null); };
  const startEdit = (home) => {
    setForm({
      ...EMPTY, ...home,
      model_name: home.model_name ?? '', manufacturer: home.manufacturer ?? '',
      serial_number: home.serial_number ?? '',
      sale_price: home.public_sale_price || '',
      bedrooms: home.beds ?? home.bedrooms ?? '', bathrooms: home.baths ?? home.bathrooms ?? '',
      sqft: home.sqft ?? '', features: (home.features || []).join(', '),
    });
    setEditing(home.id);
    setMessage(null);
  };

  const save = async () => {
    if (!form.model_name.trim()) { setMessage({ type: 'error', text: 'Model name is required.' }); return; }
    const publicPrice = Number(form.sale_price || 0);
    if (!Number.isFinite(publicPrice) || publicPrice < 0) {
      setMessage({ type: 'error', text: 'Public sale price must be a nonnegative number.' });
      return;
    }
    if (Math.abs(publicPrice * 100 - Math.round(publicPrice * 100)) > 0.000001) {
      setMessage({ type: 'error', text: 'Public sale price can have at most two decimal places.' });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const isNew = editing === 'new';
      const res = await adminFetch(isNew ? '/api/inventory' : `/api/inventory/${encodeURIComponent(editing)}`, {
        method: isNew ? 'POST' : 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toPayload(form)),
      });
      const data = await res.json();
      if (data.success) {
        setMessage({ type: 'ok', text: isNew ? 'Home added.' : 'Home updated.' });
        setEditing(null);
        await load();
      } else {
        setMessage({ type: 'error', text: safeUserMessage(extractErrorMessage(data), 'Save failed.') });
      }
    } catch {
      setMessage({ type: 'error', text: 'Save failed.' });
    } finally {
      setSaving(false);
    }
  };

  const retire = async () => {
    const home = confirmRetire;
    if (!home) return;
    setRetiring(true);
    try {
      const res = await adminFetch(`/api/inventory/${encodeURIComponent(home.id)}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        setMessage({
          type: 'ok',
          text: `“${friendlyModelName(home.model_name)}” is off the website. To bring it back, tick “Include sold/archived homes” and click “Put back on website”.`,
        });
        await load();
      } else {
        setMessage({ type: 'error', text: safeUserMessage(extractErrorMessage(data), 'Could not remove that home. Please try again.') });
      }
    } catch {
      setMessage({ type: 'error', text: 'Could not remove that home. Please try again.' });
    } finally {
      setRetiring(false);
      setConfirmRetire(null);
    }
  };

  const restore = async (home) => {
    try {
      const res = await adminFetch(`/api/inventory/${encodeURIComponent(home.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'AVAILABLE' }),
      });
      const data = await res.json();
      if (data.success) {
        setMessage({ type: 'ok', text: `“${friendlyModelName(home.model_name)}” is back on the website.` });
        await load();
      } else {
        setMessage({ type: 'error', text: safeUserMessage(extractErrorMessage(data), 'Could not put that home back. Please try again.') });
      }
    } catch {
      setMessage({ type: 'error', text: 'Could not put that home back. Please try again.' });
    }
  };

  const shownHomes = staffVisibleHomes(homes, { includeInactive });
  const hiddenInactiveCount = staffVisibleHomes(homes, { includeInactive: true }).length - staffVisibleHomes(homes).length;

  const field = (label, key, props = {}) => (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-[var(--cp-muted)]">{label}</span>
      <input
        className="rounded-lg border border-[var(--cp-border)] bg-[var(--cp-surface)] px-3 py-2 text-[var(--cp-text)]"
        value={form[key] ?? ''} onChange={(e) => setForm({ ...form, [key]: e.target.value })} {...props}
      />
    </label>
  );

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 text-[var(--cp-text)]">
      <div className="flex items-center justify-between mb-4">
        <button onClick={onBack} className="text-sm text-[var(--cp-muted)] hover:text-[var(--cp-text)]">← Back</button>
        <h1 className="text-xl font-semibold flex items-center gap-2"><ClipboardList size={20} /> Manage Homes</h1>
        <button onClick={startNew} className="inline-flex items-center gap-1 rounded-lg bg-[var(--cp-accent)] px-3 py-1.5 text-sm text-white">
          <Plus size={16} /> Add Home
        </button>
      </div>

      {message && (
        <div className={`mb-3 rounded-lg px-3 py-2 text-sm ${message.type === 'ok' ? 'bg-green-700/30 text-green-300' : 'bg-red-700/30 text-red-300'}`}>
          {message.text}
        </div>
      )}

      {editing && (
        <div className="mb-5 rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface)] p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-medium">{editing === 'new' ? 'Add a home' : 'Edit home'}</h2>
            <button onClick={() => setEditing(null)} aria-label="Close"><X size={18} /></button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {field('Model name *', 'model_name')}
            {field('Manufacturer', 'manufacturer')}
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--cp-muted)]">Classification</span>
              <select className="rounded-lg border border-[var(--cp-border)] bg-[var(--cp-surface)] px-3 py-2"
                value={form.classification} onChange={(e) => setForm({ ...form, classification: e.target.value })}>
                {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--cp-muted)]">Status</span>
              <select className="rounded-lg border border-[var(--cp-border)] bg-[var(--cp-surface)] px-3 py-2"
                value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
              </select>
            </label>
            {field('Serial #', 'serial_number')}
            {field('Bedrooms', 'bedrooms', { type: 'number', min: 0 })}
            {field('Bathrooms', 'bathrooms', { type: 'number', min: 0, step: '0.5' })}
            {field('Sq ft', 'sqft', { type: 'number', min: 0 })}
            {field('Width', 'width', { type: 'number', min: 0 })}
            {field('Length', 'length', { type: 'number', min: 0 })}
            {field('Features (comma-separated)', 'features')}
            <div>
              {field('Public sale price ($)', 'sale_price', { type: 'number', min: 0, step: '0.01', 'aria-describedby': 'public-price-help' })}
              <p id="public-price-help" className="mt-1 text-xs text-[var(--cp-muted)]">Enter only the approved advertised price. Leave blank or enter 0 for Call for Price.</p>
            </div>
          </div>
          <div className="mt-4 flex gap-2">
            <button disabled={saving} onClick={save}
              className="inline-flex items-center gap-1 rounded-lg bg-[var(--cp-accent)] px-4 py-2 text-sm text-white disabled:opacity-60">
              {saving && <Loader2 size={16} className="animate-spin" />} Save
            </button>
            <button onClick={() => setEditing(null)} className="rounded-lg border border-[var(--cp-border)] px-4 py-2 text-sm">Cancel</button>
          </div>
          <p className="mt-2 text-xs text-[var(--cp-muted)]">Dealer cost is never shown or stored on the public listing. Photos are managed in the Photos tab.</p>
        </div>
      )}

      {!loading && !error && homes.length > 0 && (
        <label className="mb-3 flex items-center gap-2 text-sm cursor-pointer select-none">
          <input
            type="checkbox"
            className="h-4 w-4"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
          />
          Include sold/archived homes
          {!includeInactive && hiddenInactiveCount > 0 && (
            <span className="text-[var(--cp-muted)]">({hiddenInactiveCount} hidden)</span>
          )}
        </label>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-[var(--cp-muted)]"><Loader2 size={16} className="animate-spin" /> Loading homes…</div>
      ) : error ? (
        <div className="text-red-400">{error}</div>
      ) : homes.length === 0 ? (
        <div className="text-[var(--cp-muted)]">No homes yet. Click “Add Home” to create one.</div>
      ) : shownHomes.length === 0 ? (
        <div className="text-[var(--cp-muted)]">No homes for sale right now. Tick “Include sold/archived homes” to see the rest.</div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[var(--cp-border)]">
          <table className="w-full text-sm">
            <thead className="text-left text-[var(--cp-muted)] border-b border-[var(--cp-border)]">
              <tr><th className="px-3 py-2">Home</th><th className="px-3 py-2">Manufacturer</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Beds/Baths</th><th className="px-3 py-2 text-right">Actions</th></tr>
            </thead>
            <tbody>
              {shownHomes.map((h) => {
                const dupes = possibleDuplicateIds(h);
                const stock = stockLabel(h);
                const isRetired = String(h.status || '').toUpperCase() === 'RETIRED';
                return (
                  <tr key={h.id} className="border-b border-[var(--cp-border)]/50 align-top">
                    <td className="px-3 py-2">
                      <div className="font-medium break-words">{friendlyModelName(h.model_name)}</div>
                      {stock && <div className="text-xs text-[var(--cp-muted)]">{stock}</div>}
                      {dupes.length > 0 && (
                        <div className="mt-1 inline-flex items-start gap-1 rounded-md bg-amber-500/15 px-2 py-1 text-xs text-amber-200" data-testid="possible-duplicate">
                          <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
                          <span>Possible duplicate: this home is saved more than once (also record {dupes.join(', ')}). Shown once here; ask the owner before removing either.</span>
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-[var(--cp-muted)]">{h.manufacturer || '—'}</td>
                    <td className="px-3 py-2">{h.status ? statusLabel(h.status) : '—'}</td>
                    <td className="px-3 py-2 text-[var(--cp-muted)]">{(h.beds ?? h.bedrooms ?? '—')}/{(h.baths ?? h.bathrooms ?? '—')}</td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-3 whitespace-nowrap">
                        <button onClick={() => startEdit(h)} className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-medium text-[var(--cp-accent)] hover:bg-[var(--cp-surface)]" aria-label="Edit"><Pencil size={14} /> Edit</button>
                        {onNavigate && <button onClick={() => onNavigate('photos')} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[var(--cp-muted)] hover:bg-[var(--cp-surface)]" aria-label="Photos"><Camera size={14} /> Photos</button>}
                        <span className="ml-3 border-l border-[var(--cp-border)] pl-4" data-testid="retire-group">
                          {isRetired ? (
                            <button onClick={() => restore(h)} className="inline-flex items-center gap-1 rounded-md border border-[var(--cp-border)] px-2.5 py-1 text-xs text-[var(--cp-text)] hover:bg-[var(--cp-surface)]">
                              <Undo2 size={13} /> Put back on website
                            </button>
                          ) : (
                            <button onClick={() => { setConfirmRetire(h); setMessage(null); }} className="inline-flex items-center gap-1 rounded-md border border-red-500/50 px-2.5 py-1 text-xs text-red-400 hover:bg-red-500/10">
                              <EyeOff size={13} /> Remove from website
                            </button>
                          )}
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={!!confirmRetire}
        title="Remove this home from the website? You can bring it back later."
        message={confirmRetire ? `“${friendlyModelName(confirmRetire.model_name)}” will stop showing to customers. Nothing is deleted.` : ''}
        confirmLabel="Yes, remove it"
        cancelLabel="No, keep it"
        danger
        busy={retiring}
        onConfirm={retire}
        onCancel={() => setConfirmRetire(null)}
      />
    </div>
  );
}
