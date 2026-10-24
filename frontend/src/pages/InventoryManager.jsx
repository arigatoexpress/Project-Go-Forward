import React, { useState, useEffect, useCallback } from 'react';
import { ClipboardList, Plus, Pencil, EyeOff, Undo2, Loader2, X, AlertTriangle } from 'lucide-react';
import adminFetch from '../adminFetch';
import ConfirmDialog from '../components/ConfirmDialog';
import RealPhotoUploader from '../components/RealPhotoUploader';
import { extractErrorMessage, safeUserMessage } from '../utils/apiError';
import {
  friendlyModelName, possibleDuplicateIds, staffVisibleHomes, statusLabel, stockLabel,
} from '../utils/inventoryDisplay';

// Staff listing editor. One screen adds or changes a home and its real photos.
// Cost fields are never shown or sent.

const STATUSES = ['AVAILABLE', 'PENDING', 'RESERVED', 'SOLD', 'RETIRED'];
const CLASSIFICATIONS = ['Single Wide', 'Double Wide'];

const EMPTY = {
  model_name: '', manufacturer: '', classification: 'Single Wide', status: 'AVAILABLE',
  serial_number: '', bedrooms: '', bathrooms: '', sqft: '', width: '', length: '',
  is_new: true, features: '', sale_price: '',
};

const NAME_REQUIRED = 'Type the home\'s name before saving.';
const PRICE_NEGATIVE = 'The price cannot be less than zero. Leave it blank if customers should call for the price.';
const PRICE_CENTS = 'Use dollars and cents only, like 89900 or 89900.50.';
const LOAD_ERROR = 'The home list did not load. Check the internet connection and try again.';
const SAVE_ERROR = 'This home was not saved. Check the connection and try again.';
const SAVED_WITH_PRICE = 'Saved. Customers will see this update on the website.';
const SAVED_CALL_FOR_PRICE = 'Saved. Customers will see Call for Price until a price is saved here.';

function websitePriceLabel(home) {
  const amount = Number(home?.public_sale_price);
  if (!Number.isFinite(amount) || amount <= 0) return 'Call for Price';
  return amount.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

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
  const [message, setMessage] = useState(null);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [confirmRetire, setConfirmRetire] = useState(null);
  const [retiring, setRetiring] = useState(false);
  const [showMore, setShowMore] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await adminFetch('/api/inventory?status=&limit=500');
      const data = await res.json();
      if (data.success) setHomes(data.inventory || []);
      else setError(safeUserMessage(extractErrorMessage(data), LOAD_ERROR));
    } catch {
      setError(LOAD_ERROR);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const startNew = () => {
    setForm(EMPTY);
    setEditing('new');
    setShowMore(false);
    setMessage(null);
  };
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
    setShowMore(false);
    setMessage(null);
  };

  const save = async () => {
    if (!form.model_name.trim()) { setMessage({ type: 'error', text: NAME_REQUIRED }); return; }
    const publicPrice = Number(form.sale_price || 0);
    if (!Number.isFinite(publicPrice) || publicPrice < 0) {
      setMessage({ type: 'error', text: PRICE_NEGATIVE });
      return;
    }
    if (Math.abs(publicPrice * 100 - Math.round(publicPrice * 100)) > 0.000001) {
      setMessage({ type: 'error', text: PRICE_CENTS });
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
        const savedId = data.id || data.inventory?.id || editing;
        if (isNew && savedId && savedId !== 'new') setEditing(savedId);
        const priceNote = publicPrice > 0 ? SAVED_WITH_PRICE : SAVED_CALL_FOR_PRICE;
        setMessage({
          type: 'ok',
          text: isNew
            ? `${priceNote} Now add photos of the real home below. Customers will not see floor plans from this box.`
            : priceNote,
        });
        await load();
      } else {
        setMessage({ type: 'error', text: safeUserMessage(extractErrorMessage(data), SAVE_ERROR) });
      }
    } catch {
      setMessage({ type: 'error', text: SAVE_ERROR });
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
          text: `“${friendlyModelName(home.model_name)}” is off the website. To bring it back, tick “Show sold homes too” and tap “Put back on website”.`,
        });
        await load();
      } else {
        setMessage({ type: 'error', text: safeUserMessage(extractErrorMessage(data), 'That home is still on the website. Try again.') });
      }
    } catch {
      setMessage({ type: 'error', text: 'That home is still on the website. Check the connection and try again.' });
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
        setMessage({ type: 'error', text: safeUserMessage(extractErrorMessage(data), 'That home is still hidden. Try again.') });
      }
    } catch {
      setMessage({ type: 'error', text: 'That home is still hidden. Check the connection and try again.' });
    }
  };

  const shownHomes = staffVisibleHomes(homes, { includeInactive });
  const hiddenInactiveCount = staffVisibleHomes(homes, { includeInactive: true }).length - staffVisibleHomes(homes).length;
  const editingHome = homes.find((home) => String(home.id) === String(editing));
  const photoHomeId = editing && editing !== 'new' ? editing : '';
  const photoHomeName = friendlyModelName(form.model_name || editingHome?.model_name || '');

  const field = (label, key, props = {}, helpId, help) => (
    <div className="flex flex-col gap-1">
      <label className="flex flex-col gap-1 text-base" htmlFor={`home-${key}`}>
        <span className="font-semibold">{label}</span>
        <input
          id={`home-${key}`}
          className="min-h-12 rounded-lg border border-[var(--cp-border)] bg-[var(--cp-surface)] px-3 py-3 text-base text-[var(--cp-text)]"
          value={form[key] ?? ''}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          aria-describedby={help ? helpId : undefined}
          {...props}
        />
      </label>
      {help && <p id={helpId} className="text-base leading-relaxed text-[var(--cp-muted)]">{help}</p>}
    </div>
  );

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 text-[var(--cp-text)]">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <button type="button" onClick={onBack} className="min-h-12 text-left text-base text-[var(--cp-muted)] hover:text-[var(--cp-text)]">← Back</button>
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2"><ClipboardList size={24} /> Homes on the website</h1>
          <p className="mt-1 text-base leading-relaxed text-[var(--cp-muted)]">
            Add a home or change one. Photos of the real home go on the same screen.
            A blank price shows Call for Price. Each home in the list says so until a price is saved.
          </p>
        </div>
        <button type="button" onClick={startNew} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-[var(--cp-accent)] px-4 text-base font-semibold text-white">
          <Plus size={20} /> Add a home
        </button>
      </div>

      {message && !editing && (
        <div role={message.type === 'ok' ? 'status' : 'alert'} className={`mb-3 rounded-lg px-4 py-3 text-base leading-relaxed ${message.type === 'ok' ? 'bg-green-700/30 text-green-200' : 'bg-red-700/30 text-red-200'}`}>
          {message.text}
        </div>
      )}

      {editing && (
        <div className="mb-5 rounded-xl border border-[var(--cp-border)] bg-[var(--cp-surface)] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-xl font-semibold">{editing === 'new' ? 'Add a home' : 'Change this home'}</h2>
            <button type="button" onClick={() => setEditing(null)} aria-label="Close" className="inline-flex min-h-12 min-w-12 items-center justify-center rounded-lg border border-[var(--cp-border)]"><X size={22} /></button>
          </div>
          {message && (
            <div role={message.type === 'ok' ? 'status' : 'alert'} className={`mb-3 rounded-lg px-4 py-3 text-base leading-relaxed ${message.type === 'ok' ? 'bg-green-700/30 text-green-200' : 'bg-red-700/30 text-red-200'}`}>
              {message.text}
            </div>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {field('Home name', 'model_name', { required: true }, 'home-name-help', 'The name customers see, such as Champion or Big Blue.')}
            <div>
              {field('Price on the website ($)', 'sale_price', { type: 'number', min: 0, step: '0.01', inputMode: 'decimal' }, 'public-price-help', 'The price you are allowed to advertise. Leave blank to show Call for Price.')}
            </div>
          </div>
          <fieldset className="mt-4">
            <legend className="text-base font-semibold">Is this home new?</legend>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <button
                type="button"
                aria-pressed={!!form.is_new}
                onClick={() => setForm({ ...form, is_new: true })}
                className={`min-h-12 rounded-lg border px-4 text-base font-semibold ${form.is_new ? 'border-[var(--cp-accent)] bg-[var(--cp-accent)] text-white' : 'border-[var(--cp-border)]'}`}
              >
                Brand new
              </button>
              <button
                type="button"
                aria-pressed={!form.is_new}
                onClick={() => setForm({ ...form, is_new: false })}
                className={`min-h-12 rounded-lg border px-4 text-base font-semibold ${!form.is_new ? 'border-[var(--cp-accent)] bg-[var(--cp-accent)] text-white' : 'border-[var(--cp-border)]'}`}
              >
                Pre-owned
              </button>
            </div>
          </fieldset>

          <button
            type="button"
            className="mt-4 min-h-12 text-base font-semibold underline"
            aria-expanded={showMore}
            onClick={() => setShowMore((open) => !open)}
          >
            {showMore ? 'Hide extra details' : 'More details (beds, size, builder)'}
          </button>
          {showMore && (
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              {field('Who built it', 'manufacturer', {}, 'builder-help', 'Clayton or Champion. Leave blank if you are not sure.')}
              <label className="flex flex-col gap-1 text-base" htmlFor="home-classification">
                <span className="font-semibold">Home type</span>
                <select id="home-classification" className="min-h-12 rounded-lg border border-[var(--cp-border)] bg-[var(--cp-surface)] px-3 py-3 text-base"
                  value={form.classification} onChange={(e) => setForm({ ...form, classification: e.target.value })}>
                  {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-base" htmlFor="home-status">
                <span className="font-semibold">Where it stands</span>
                <select id="home-status" className="min-h-12 rounded-lg border border-[var(--cp-border)] bg-[var(--cp-surface)] px-3 py-3 text-base"
                  value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                  {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
                </select>
                <p className="text-base leading-relaxed text-[var(--cp-muted)]">For sale, on hold, pending, sold, or off the website.</p>
              </label>
              {field('Serial number', 'serial_number', {}, 'serial-help', 'On the home\'s data plate. Optional.')}
              {field('Bedrooms', 'bedrooms', { type: 'number', min: 0, inputMode: 'numeric' })}
              {field('Bathrooms', 'bathrooms', { type: 'number', min: 0, step: '0.5', inputMode: 'decimal' })}
              {field('Square feet', 'sqft', { type: 'number', min: 0, inputMode: 'numeric' })}
              {field('Width in feet', 'width', { type: 'number', min: 0, inputMode: 'decimal' })}
              {field('Length in feet', 'length', { type: 'number', min: 0, inputMode: 'decimal' })}
              {field('What comes with it', 'features', {}, 'features-help', 'Separate with commas, such as porch, island kitchen.')}
            </div>
          )}

          <RealPhotoUploader
            homeId={photoHomeId}
            homeName={photoHomeName === 'Unnamed home' ? 'this home' : photoHomeName}
            preowned={form.is_new === false}
          />

          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <button type="button" disabled={saving} onClick={save}
              className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-[var(--cp-accent)] px-4 text-base font-semibold text-white disabled:opacity-60">
              {saving && <Loader2 size={18} className="animate-spin" />} Save this home
            </button>
            <button type="button" onClick={() => setEditing(null)} className="min-h-12 rounded-lg border border-[var(--cp-border)] px-4 text-base">Done</button>
          </div>
          <p className="mt-3 text-base leading-relaxed text-[var(--cp-muted)]">Dealer cost is never shown or stored on the public listing.</p>
        </div>
      )}

      {!loading && !error && homes.length > 0 && (
        <label className="mb-3 flex min-h-12 items-center gap-3 text-base cursor-pointer select-none">
          <input
            type="checkbox"
            className="h-6 w-6"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
          />
          Show sold homes too
          {!includeInactive && hiddenInactiveCount > 0 && (
            <span className="text-[var(--cp-muted)]">({hiddenInactiveCount} hidden)</span>
          )}
        </label>
      )}

      {loading ? (
        <div role="status" className="flex items-center gap-2 text-base text-[var(--cp-muted)]"><Loader2 size={18} className="animate-spin" aria-hidden="true" /> Loading homes…</div>
      ) : error ? (
        <div role="alert" className="rounded-lg bg-red-700/20 px-4 py-3 text-base text-red-200">{error}</div>
      ) : homes.length === 0 ? (
        <div role="status" className="text-base text-[var(--cp-muted)]">No homes yet. Tap “Add a home” to put the first one on the website.</div>
      ) : shownHomes.length === 0 ? (
        <div role="status" className="text-base text-[var(--cp-muted)]">No homes for sale right now. Tick “Show sold homes too” to see the rest.</div>
      ) : (
        <ul className="flex flex-col gap-3">
          {shownHomes.map((h) => {
            const dupes = possibleDuplicateIds(h);
            const stock = stockLabel(h);
            const isRetired = String(h.status || '').toUpperCase() === 'RETIRED';
            return (
              <li key={h.id} className="rounded-xl border border-[var(--cp-border)] p-4">
                <h2 className="text-xl font-semibold break-words">{friendlyModelName(h.model_name)}</h2>
                <p className="mt-1 text-base font-semibold">{websitePriceLabel(h)}</p>
                <p className="mt-1 text-base text-[var(--cp-muted)]">
                  {[h.manufacturer, h.status ? statusLabel(h.status) : '', stock, `${h.beds ?? h.bedrooms ?? '—'} bed / ${h.baths ?? h.bathrooms ?? '—'} bath`].filter(Boolean).join(' · ')}
                </p>
                {dupes.length > 0 && (
                  <div className="mt-2 inline-flex items-start gap-2 rounded-md bg-amber-500/15 px-3 py-2 text-base text-amber-200" data-testid="possible-duplicate">
                    <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
                    <span>Possible duplicate: this home is saved more than once (also record {dupes.join(', ')}). Shown once here; ask the owner before removing either.</span>
                  </div>
                )}
                <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                  <button type="button" onClick={() => startEdit(h)} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-[var(--cp-accent)] px-4 text-base font-semibold text-white">
                    <Pencil size={18} /> Change this home
                  </button>
                  <span data-testid="retire-group">
                    {isRetired ? (
                      <button type="button" onClick={() => restore(h)} className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-lg border border-[var(--cp-border)] px-4 text-base sm:w-auto">
                        <Undo2 size={18} /> Put back on website
                      </button>
                    ) : (
                      <button type="button" onClick={() => { setConfirmRetire(h); setMessage(null); }} className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-lg border border-red-500/50 px-4 text-base text-red-300 sm:w-auto">
                        <EyeOff size={18} /> Remove from website
                      </button>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {onNavigate && (
        <p className="mt-6 text-base leading-relaxed text-[var(--cp-muted)]">
          To add photos to several homes in a row, open the{' '}
          <button type="button" className="min-h-12 font-semibold underline" onClick={() => onNavigate('photos')}>photo page</button>.
        </p>
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
