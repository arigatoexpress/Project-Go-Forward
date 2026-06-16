import React, { useState, useEffect, useCallback } from 'react';
import { Home, Plus, Pencil, Archive, Loader2, X, Camera } from 'lucide-react';
import adminFetch from '../adminFetch';

// Staff Inventory Manager — create / edit / retire homes in the in-app
// (Firestore) inventory store the public site serves when INVENTORY_SOURCE=
// firestore. Wired to the admin CRUD endpoints (POST/PUT/DELETE /api/inventory).
// Cost fields are never shown or sent — the public read path hides dealer cost.

const STATUSES = ['AVAILABLE', 'PENDING', 'RESERVED', 'SOLD', 'RETIRED'];
const CLASSIFICATIONS = ['Single Wide', 'Double Wide'];

const EMPTY = {
  model_name: '', manufacturer: '', classification: 'Single Wide', status: 'AVAILABLE',
  serial_number: '', bedrooms: '', bathrooms: '', sqft: '', width: '', length: '',
  is_new: true, features: '',
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

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await adminFetch('/api/inventory?status=&limit=500');
      const data = await res.json();
      if (data.success) setHomes(data.inventory || []);
      else setError(data.error || 'Failed to load inventory.');
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
      bedrooms: home.beds ?? home.bedrooms ?? '', bathrooms: home.baths ?? home.bathrooms ?? '',
      sqft: home.sqft ?? '', features: (home.features || []).join(', '),
    });
    setEditing(home.id);
    setMessage(null);
  };

  const save = async () => {
    if (!form.model_name.trim()) { setMessage({ type: 'error', text: 'Model name is required.' }); return; }
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
        setMessage({ type: 'error', text: data.error || 'Save failed.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'Save failed.' });
    } finally {
      setSaving(false);
    }
  };

  const retire = async (home) => {
    if (!window.confirm(`Retire "${home.model_name}"? It will drop off the public site but is kept.`)) return;
    try {
      const res = await adminFetch(`/api/inventory/${encodeURIComponent(home.id)}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) { setMessage({ type: 'ok', text: 'Home retired.' }); await load(); }
      else setMessage({ type: 'error', text: data.error || 'Retire failed.' });
    } catch {
      setMessage({ type: 'error', text: 'Retire failed.' });
    }
  };

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
        <h1 className="text-xl font-semibold flex items-center gap-2"><Home size={20} /> Manage Inventory</h1>
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
                {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            {field('Serial #', 'serial_number')}
            {field('Bedrooms', 'bedrooms', { type: 'number', min: 0 })}
            {field('Bathrooms', 'bathrooms', { type: 'number', min: 0, step: '0.5' })}
            {field('Sq ft', 'sqft', { type: 'number', min: 0 })}
            {field('Width', 'width', { type: 'number', min: 0 })}
            {field('Length', 'length', { type: 'number', min: 0 })}
            {field('Features (comma-separated)', 'features')}
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

      {loading ? (
        <div className="flex items-center gap-2 text-[var(--cp-muted)]"><Loader2 size={16} className="animate-spin" /> Loading inventory…</div>
      ) : error ? (
        <div className="text-red-400">{error}</div>
      ) : homes.length === 0 ? (
        <div className="text-[var(--cp-muted)]">No homes yet. Click “Add Home” to create one.</div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[var(--cp-border)]">
          <table className="w-full text-sm">
            <thead className="text-left text-[var(--cp-muted)] border-b border-[var(--cp-border)]">
              <tr><th className="px-3 py-2">Model</th><th className="px-3 py-2">Manufacturer</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Beds/Baths</th><th className="px-3 py-2 text-right">Actions</th></tr>
            </thead>
            <tbody>
              {homes.map((h) => (
                <tr key={h.id} className="border-b border-[var(--cp-border)]/50">
                  <td className="px-3 py-2 font-medium">{h.model_name || '—'}</td>
                  <td className="px-3 py-2 text-[var(--cp-muted)]">{h.manufacturer || '—'}</td>
                  <td className="px-3 py-2">{h.status || '—'}</td>
                  <td className="px-3 py-2 text-[var(--cp-muted)]">{(h.beds ?? h.bedrooms ?? '—')}/{(h.baths ?? h.bathrooms ?? '—')}</td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => startEdit(h)} className="inline-flex items-center gap-1 text-[var(--cp-accent)]" aria-label="Edit"><Pencil size={14} /> Edit</button>
                      {onNavigate && <button onClick={() => onNavigate('photos')} className="inline-flex items-center gap-1 text-[var(--cp-muted)]" aria-label="Photos"><Camera size={14} /> Photos</button>}
                      <button onClick={() => retire(h)} className="inline-flex items-center gap-1 text-red-400" aria-label="Retire"><Archive size={14} /> Retire</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
