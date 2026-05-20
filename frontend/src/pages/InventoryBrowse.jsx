import React, { useState, useEffect, useCallback, useRef, } from 'react';
import {
  Search, SlidersHorizontal, X, Home, Bed, Bath, Maximize2,
  Camera, Box, ChevronLeft, ChevronRight, MapPin, Phone,
  MessageCircle, Grid3X3, Loader2, Eye, ArrowUpDown, Calendar,
  DollarSign, Video, CheckCircle2, AlertCircle
} from 'lucide-react';
import { BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_ADDRESS, BUSINESS_CITY, BUSINESS_HOURS } from '../constants';
import Card from '../components/Card';
import EmptyState from '../components/EmptyState';
import StatusBadge from '../components/StatusBadge';
import { Skeleton, SkeletonCard } from '../components/Skeleton';

const MATTERPORT_BASE = "https://my.matterport.com/show/?m=";

// ─── Focus Trap Hook ───
// Traps keyboard focus inside `ref` while the modal is mounted.
// Restores focus to the previously-focused element on unmount.
function useFocusTrap(ref) {
  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    const previouslyFocused = document.activeElement;

    const FOCUSABLE = 'button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    const getFocusable = () => Array.from(container.querySelectorAll(FOCUSABLE));

    // Move focus into modal on open
    const firstFocusable = getFocusable()[0];
    if (firstFocusable) firstFocusable.focus();

    const onKeyDown = (e) => {
      if (e.key !== 'Tab') return;
      const focusable = getFocusable();
      if (focusable.length === 0) { e.preventDefault(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus(); }
      } else {
        if (document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
    };
  }, [ref]);
}

// ─── Analytics helper ───
function trackEvent(event, data = {}) {
  try {
    fetch('/api/contact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ _event: event, ...data }),
    }).catch(() => {}); // fire and forget
  } catch {
    // Analytics should never interrupt the browsing flow.
  }
}

// ─── Sort options ───
const SORT_OPTIONS = [
  { value: 'default', label: 'Default' },
  { value: 'price_low', label: 'Price: Low to High' },
  { value: 'price_high', label: 'Price: High to Low' },
  { value: 'sqft_high', label: 'Largest First' },
  { value: 'sqft_low', label: 'Smallest First' },
  { value: 'beds_high', label: 'Most Bedrooms' },
  { value: 'photos', label: 'Most Photos' },
];

// ─── Reusable utility chains ───
const PILL_BTN =
  'px-4 py-2.5 rounded-md text-sm font-medium transition border border-transparent';
const CHIP_INPUT =
  'px-3 py-2 rounded-md border border-[var(--cp-border)] text-sm bg-[var(--cp-bg-2)] text-[var(--cp-text)] focus:outline-none focus:border-[var(--cp-accent)] transition';
const FLOORPLAN_TOKENS = ['floorplan', 'floor-plan', 'floor_plan', 'floor-plans', 'floor_plans', 'floor plans'];
const PHOTO_FILENAME_TOKENS = ['bath', 'bed', 'coffee', 'exterior', 'front', 'interior', 'island', 'kitchen', 'living', 'porch', 'room', 'utility'];
const SHORT_PHOTO_FILENAME_TOKENS = ['3d', 'ext', 'int', 'kit'];
const UUID_FILENAME_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function getFloorplanUrls(home) {
  if (!home) return [];
  return [
    home.floorplan_url,
    home.floor_plan_url,
    ...(Array.isArray(home.floorplan_urls) ? home.floorplan_urls : []),
  ].filter(Boolean);
}

function isManufacturerFloorplanNamespace(url) {
  try {
    return /\/manufacturer\/[^/]+\/floorplan\/[^/]+\//i.test(new URL(url).pathname);
  } catch {
    return /\/manufacturer\/[^/]+\/floorplan\/[^/]+\//i.test(String(url));
  }
}

function looksLikePhotoFilename(filename) {
  const stem = filename.replace(/\.[a-z0-9]+$/i, '');
  if (!stem) return false;
  if (/^\d+$/.test(stem)) return true;
  if (UUID_FILENAME_RE.test(stem)) return true;
  if (/[-_\s]\d+$/.test(stem)) return true;
  const parts = stem.split(/[^a-z0-9]+/).filter(Boolean);
  if (parts.some(part => SHORT_PHOTO_FILENAME_TOKENS.includes(part))) return true;
  return PHOTO_FILENAME_TOKENS.some(token => stem.includes(token));
}

function looksLikeBareModelFloorplan(url, filename) {
  return isManufacturerFloorplanNamespace(url)
    && /\.(jpe?g|png|webp)$/i.test(filename)
    && !looksLikePhotoFilename(filename)
    && /[a-z]/i.test(filename);
}

function isFloorplanImage(url, floorplanUrls = []) {
  if (!url) return false;
  const normalized = String(url).trim().replace(/\/$/, '');
  if (floorplanUrls.some(fp => fp && String(fp).trim().replace(/\/$/, '') === normalized)) {
    return true;
  }
  const filename = decodeURIComponent(String(url).split('/').pop()?.split('?')[0] || '').toLowerCase();
  return filename.endsWith('.pdf')
    || FLOORPLAN_TOKENS.some(token => filename.includes(token))
    || looksLikeBareModelFloorplan(url, filename);
}

function getListingPhotos(home) {
  if (!home) return [];
  const floorplanUrls = getFloorplanUrls(home);
  const candidates = [
    home.image_url,
    ...(Array.isArray(home.real_photos) ? home.real_photos : []),
    ...(Array.isArray(home.gallery_images) ? home.gallery_images : []),
  ];
  return candidates.filter((photo, index, values) => (
    photo && values.indexOf(photo) === index && !isFloorplanImage(photo, floorplanUrls)
  ));
}

function getHomeImage(home) {
  return getListingPhotos(home)[0] || '';
}

function displayPrice(home) {
  return home?.display_price && home.display_price !== 'Call for Price'
    ? home.display_price
    : 'Call for Price';
}

function getAvailabilityKind(home) {
  if (home?.inventory_kind) return home.inventory_kind;
  if (home?.status === 'Orderable') return 'orderable_floorplan';
  if (home?.status === 'Pre-Owned') return 'pre_owned';
  return 'available_now';
}

function getLegacyQuoteUrl(home) {
  return home?.quote_url || home?.legacy_actions?.quote_url || '';
}

const LEGACY_INVENTORY_ALIASES = {
  // The legacy WordPress site exposes The Nassau as inventory id 42155, while
  // the current production seed uses a local id for the same public home.
  42155: ['tho-2024-001', 'nassau'],
};

function normalizeInventoryText(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function getLegacyInventoryIntent(pathname) {
  const path = String(pathname || '');
  const detailMatch = path.match(/\/inventory-detail\/(\d+)/i);
  if (detailMatch) return { id: detailMatch[1], intent: 'detail' };
  const quoteCardMatch = path.match(/\/quote\/inventory\/\d+\/(\d+)/i);
  if (quoteCardMatch) return { id: quoteCardMatch[1], intent: 'quote' };
  const quoteDetailMatch = path.match(/\/quote\/inventory\/(\d+)\/dealer\/\d+/i);
  if (quoteDetailMatch) return { id: quoteDetailMatch[1], intent: 'quote' };
  return null;
}

function resolveLegacyHome(homes, legacyId) {
  const aliases = LEGACY_INVENTORY_ALIASES[legacyId] || [];
  const wanted = [legacyId, ...aliases].map(normalizeInventoryText).filter(Boolean);
  return homes.find(home => {
    const id = normalizeInventoryText(home.id);
    const legacyInventoryId = normalizeInventoryText(home.legacy_inventory_id);
    const name = normalizeInventoryText(home.model_name);
    return wanted.some(value => id === value || legacyInventoryId === value || name.includes(value));
  });
}

function InventoryMetric({ icon, label, value, tone = 'accent' }) {
  const toneMap = {
    accent: 'text-[var(--cp-accent)] bg-[var(--cp-accent-dim)]',
    blue: 'text-[var(--cp-secondary)] bg-[var(--cp-secondary-dim)]',
    warn: 'text-[var(--cp-warn)] bg-[var(--tho-warning-light)]',
  };
  const Icon = icon;
  return (
    <div className="rounded-md border border-[var(--cp-border)] bg-[var(--cp-panel)] px-4 py-3">
      <div className="flex items-center gap-2">
        <span className={`inline-flex h-8 w-8 items-center justify-center rounded-md ${toneMap[tone]}`}>
          <Icon size={16} />
        </span>
        <div>
          <div className="font-mono text-xl font-bold text-[var(--cp-text)]">{value}</div>
          <div className="text-[11px] uppercase text-[var(--cp-muted)]">{label}</div>
        </div>
      </div>
    </div>
  );
}

function FeaturedHomeSpotlight({ home, onClick, onScheduleTour }) {
  if (!home) return null;
  const specs = home.specs || {};
  const image = getHomeImage(home);
  const quoteUrl = getLegacyQuoteUrl(home);
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--cp-border-light)] bg-[var(--cp-panel)] shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
      <button
        type="button"
        onClick={onClick}
        className="group block w-full text-left"
      >
        <div className="relative h-[260px] overflow-hidden bg-[var(--cp-bg-2)]">
          {image ? (
            <img
              src={image}
              alt={home.model_name}
              className="h-full w-full object-cover transition duration-500 group-hover:scale-105"
            />
          ) : (
            <div className="flex flex-col h-full items-center justify-center gap-3 bg-[var(--cp-bg-2)]">
              <Camera size={48} className="text-[var(--cp-faint)]" />
              <span className="text-sm font-semibold text-[var(--cp-muted)] uppercase tracking-wider">Photos Coming Soon</span>
            </div>
          )}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent p-4">
            <div className="mb-2 inline-flex items-center gap-1.5 rounded-md bg-[var(--cp-accent)] px-2.5 py-1 text-[11px] font-bold uppercase text-[var(--cp-bg)]">
              <CheckCircle2 size={12} />
              Featured Live Listing
            </div>
            <h2 className="text-xl font-bold text-white">{home.model_name}</h2>
            <p className="text-sm text-white/75">{home.manufacturer || 'Texas Home Outlet'}</p>
          </div>
        </div>
      </button>
      <div className="space-y-4 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="font-mono text-2xl font-bold text-[var(--cp-accent)]">
            {displayPrice(home)}
          </div>
          <StatusBadge status={home.status} kind="home" size="md" />
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-sm">
          <div className="rounded-md bg-[var(--cp-bg-2)] p-2 text-[var(--cp-text-secondary)]">
            <Bed size={15} className="mx-auto mb-1 text-[var(--cp-muted)]" />
            {specs.beds || '-'} bed
          </div>
          <div className="rounded-md bg-[var(--cp-bg-2)] p-2 text-[var(--cp-text-secondary)]">
            <Bath size={15} className="mx-auto mb-1 text-[var(--cp-muted)]" />
            {specs.baths || '-'} bath
          </div>
          <div className="rounded-md bg-[var(--cp-bg-2)] p-2 text-[var(--cp-text-secondary)]">
            <Maximize2 size={15} className="mx-auto mb-1 text-[var(--cp-muted)]" />
            {specs.sq_ft ? specs.sq_ft.toLocaleString() : '-'} sqft
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={onClick}
            className="inline-flex items-center justify-center gap-2 rounded-md border border-[var(--cp-border-light)] px-3 py-2 text-sm font-semibold text-[var(--cp-text)] transition hover:border-[var(--cp-secondary)] hover:text-[var(--cp-secondary)]"
          >
            <Eye size={15} />
            Details
          </button>
          {quoteUrl ? (
            <a
              href={quoteUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-md bg-[var(--cp-accent)] px-3 py-2 text-sm font-semibold text-[var(--cp-bg)] no-underline transition hover:bg-[var(--cp-accent-hot)]"
            >
              <DollarSign size={15} />
              Quote
            </a>
          ) : (
            <button
              type="button"
              onClick={onScheduleTour}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-[var(--cp-accent)] px-3 py-2 text-sm font-semibold text-[var(--cp-bg)] transition hover:bg-[var(--cp-accent-hot)]"
            >
              <Calendar size={15} />
              Tour
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Admin-only inventory analytics panel. Renders only for authenticated
// admins. Uses /api/admin/inventory/analytics (gated server-side by
// require_admin); the httpOnly cookie is sent automatically.
function AdminInventoryPanel({ enabled }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    fetch('/api/admin/inventory/analytics', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(d => { if (alive && d?.success) setData(d); })
      .catch(e => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [enabled]);

  if (!data && !loading) return null;

  const fmtMoney = (n) => n == null ? '—' : `$${Math.round(n).toLocaleString()}`;

  return (
    <div style={{
      maxWidth: 1200, margin: '12px auto', padding: 16,
      background: 'var(--cp-panel)', border: '1px solid var(--cp-border)', borderRadius: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--cp-accent)' }}>
          Inventory Analytics (admin)
        </div>
        <div style={{ fontSize: 10, color: 'var(--cp-faint)' }}>cached 5m</div>
      </div>
      {loading && <div style={{ fontSize: 12, color: 'var(--cp-muted)' }}>Loading...</div>}
      {error && <div style={{ fontSize: 12, color: 'var(--cp-danger)' }}>Error: {error}</div>}
      {data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 12 }}>
            {[
              { label: 'Total', value: data.totals?.total },
              { label: 'Available', value: data.totals?.available, color: '#22c55e' },
              { label: 'Pending', value: data.totals?.pending, color: '#f59e0b' },
              { label: 'Reserved', value: data.totals?.reserved, color: '#3b82f6' },
              { label: 'Sold (30d)', value: data.totals?.sold_last_30d, color: '#8b5cf6' },
              { label: 'Median Price', value: fmtMoney(data.median_sale_price), color: '#06b6d4' },
              { label: 'Median Days On Lot', value: data.median_time_on_lot_days, color: '#14b8a6' },
            ].map(m => (
              <div key={m.label} style={{ background: 'var(--cp-bg-2)', border: '1px solid var(--cp-border)', borderRadius: 6, padding: '8px 12px' }}>
                <div style={{ fontSize: 10, color: 'var(--cp-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{m.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: m.color || '#222', marginTop: 2 }}>
                  {m.value ?? '—'}
                </div>
              </div>
            ))}
          </div>

          {data.by_manufacturer?.length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: 'var(--cp-muted)', marginBottom: 6, fontWeight: 600 }}>By Manufacturer</div>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr', fontSize: 11, color: 'var(--cp-muted)', padding: '4px 8px', borderBottom: '1px solid var(--cp-border)' }}>
                <span>Manufacturer</span>
                <span style={{ textAlign: 'right' }}>Total</span>
                <span style={{ textAlign: 'right' }}>Available</span>
                <span style={{ textAlign: 'right' }}>Sold</span>
                <span style={{ textAlign: 'right' }}>Median Price</span>
              </div>
              {data.by_manufacturer.slice(0, 12).map(m => (
                <div key={m.manufacturer} style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr', fontSize: 12, padding: '4px 8px', borderBottom: '1px solid var(--cp-border)' }}>
                  <span style={{ color: 'var(--cp-text)', fontWeight: 500 }}>{m.manufacturer}</span>
                  <span style={{ textAlign: 'right' }}>{m.count}</span>
                  <span style={{ textAlign: 'right', color: '#22c55e' }}>{m.available}</span>
                  <span style={{ textAlign: 'right', color: '#8b5cf6' }}>{m.sold}</span>
                  <span style={{ textAlign: 'right' }}>{fmtMoney(m.median_price)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}


export default function InventoryBrowse({ adminAuthed = false, onAskTex, onCreateAd }) {
  const [homes, setHomes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedHome, setSelectedHome] = useState(null);
  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
  const [activeCategory, setActiveCategory] = useState('all');
  const [showTour, setShowTour] = useState(false);
  const [sortBy, setSortBy] = useState('default');

  // Filters
  const [filters, setFilters] = useState({
    status: '',
    beds: '',
    baths: '',
    minPrice: '',
    maxPrice: '',
    classification: '',
  });
  const [showFilters, setShowFilters] = useState(false);

  // Lead capture form
  const [showLeadForm, setShowLeadForm] = useState(false);
  const [leadFormHome, setLeadFormHome] = useState(null);
  const [leadFormType, setLeadFormType] = useState('tour'); // 'tour' | 'price'
  const legacyRouteHandledRef = useRef(false);

  const fetchInventory = useCallback(async () => {
    try {
      setLoading(true);
      const resp = await fetch('/api/marketing/inventory-context');
      if (!resp.ok) throw new Error('Failed to load inventory');
      const data = await resp.json();
      setHomes(data.homes || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchInventory();
  }, [fetchInventory]);

  // Enhanced search — matches name, manufacturer, features, classification, specs
  const matchesSearch = (home, q) => {
    const query = q.toLowerCase();
    const name = (home.model_name || '').toLowerCase();
    const mfr = (home.manufacturer || '').toLowerCase();
    const classification = (home.classification || '').toLowerCase();
    const availability = `${home.status || ''} ${home.availability_label || ''} ${getAvailabilityKind(home)}`.toLowerCase();
    const features = (home.features || []).join(' ').toLowerCase();
    const dims = (home.specs?.dimensions || '').toLowerCase();
    const beds = String(home.specs?.beds || '');
    const baths = String(home.specs?.baths || '');

    return (
      name.includes(query) ||
      mfr.includes(query) ||
      classification.includes(query) ||
      availability.includes(query) ||
      features.includes(query) ||
      dims.includes(query) ||
      (query.match(/^\d+\s*bed/) && beds === query.match(/^(\d+)/)[1]) ||
      (query.match(/^\d+\s*bath/) && baths === query.match(/^(\d+)/)[1]) ||
      (query === 'single wide' && classification === 'single wide') ||
      (query === 'double wide' && classification === 'double wide') ||
      (query === 'park model' && name.includes('park model')) ||
      (query === '3d tour' && !!home.matterport_id)
    );
  };

  // Filter and search logic
  const filteredHomes = homes.filter(home => {
    if (searchQuery && !matchesSearch(home, searchQuery)) return false;
    if (filters.status && getAvailabilityKind(home) !== filters.status) return false;
    if (filters.beds) {
      const beds = home.specs?.beds || 0;
      if (beds < parseInt(filters.beds)) return false;
    }
    if (filters.baths) {
      const baths = home.specs?.baths || 0;
      if (baths < parseInt(filters.baths)) return false;
    }
    if (filters.minPrice) {
      const price = home.price_value || 0;
      if (price > 0 && price < parseFloat(filters.minPrice)) return false;
    }
    if (filters.maxPrice) {
      const price = home.price_value || 0;
      if (price > 0 && price > parseFloat(filters.maxPrice)) return false;
    }
    if (filters.classification) {
      if ((home.classification || '').toLowerCase() !== filters.classification.toLowerCase()) return false;
    }
    return true;
  });

  // Sort
  const sortedHomes = [...filteredHomes].sort((a, b) => {
    const priceA = a.price_value || 0;
    const priceB = b.price_value || 0;
    const sqftA = a.specs?.sq_ft || 0;
    const sqftB = b.specs?.sq_ft || 0;
    const bedsA = a.specs?.beds || 0;
    const bedsB = b.specs?.beds || 0;
    const photosA = getListingPhotos(a).length;
    const photosB = getListingPhotos(b).length;

    switch (sortBy) {
      case 'price_low':
        if (!priceA && !priceB) return 0;
        if (!priceA) return 1;
        if (!priceB) return -1;
        return priceA - priceB;
      case 'price_high':
        if (!priceA && !priceB) return 0;
        if (!priceA) return 1;
        if (!priceB) return -1;
        return priceB - priceA;
      case 'sqft_high': return sqftB - sqftA;
      case 'sqft_low': return sqftA - sqftB;
      case 'beds_high': return bedsB - bedsA;
      case 'photos': return photosB - photosA;
      default: return 0;
    }
  });

  const clearFilters = () => {
    setFilters({ status: '', beds: '', baths: '', minPrice: '', maxPrice: '', classification: '' });
    setSearchQuery('');
    setSortBy('default');
  };

  const hasActiveFilters = Object.values(filters).some(v => v !== '') || searchQuery;

  // Photo helpers for detail modal.
  //
  // After PR #43 (`feat/inventory-photo-classification`), the backend
  // guarantees:
  //   - `image_url` is a non-floorplan exterior URL (or empty string).
  //   - `real_photos` lists exteriors first, then floorplans appended.
  //   - `floorplan_url` / `floor_plan_url` is its own dedicated field.
  //
  // For the Photos gallery we want exteriors only — floorplans live in
  // their own dedicated "Floorplan" tab. We exclude both spellings of the
  // floorplan URL from the gallery so the user never sees a floorplan
  // mixed in with the listing photos.
  const getPhotosForCategory = useCallback((home) => {
    if (!home) return [];
    const allPhotos = getListingPhotos(home);

    if (activeCategory === 'all' || !home.image_categories) return allPhotos;

    const catFiles = home.image_categories[activeCategory] || [];
    if (catFiles.length === 0) return allPhotos;

    return catFiles.map(f => {
      if (f.startsWith('http')) return f;
      const match = allPhotos.find(url => url.includes(f));
      return match || f;
    });
  }, [activeCategory]);

  const openDetail = useCallback((home) => {
    setSelectedHome(home);
    setActivePhotoIndex(0);
    setActiveCategory('all');
    setShowTour(false);
    trackEvent('home_viewed', { home: home.model_name, status: home.status });
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedHome(null);
    setActivePhotoIndex(0);
    setActiveCategory('all');
    setShowTour(false);
  }, []);

  const photos = getPhotosForCategory(selectedHome);

  const nextPhoto = useCallback(() => {
    if (photos.length > 0) {
      setActivePhotoIndex((prev) => (prev + 1) % photos.length);
    }
  }, [photos.length]);

  const prevPhoto = useCallback(() => {
    if (photos.length > 0) {
      setActivePhotoIndex((prev) => (prev - 1 + photos.length) % photos.length);
    }
  }, [photos.length]);

  // Keyboard navigation
  useEffect(() => {
    if (!selectedHome) return;
    const handler = (e) => {
      if (e.key === 'ArrowRight') nextPhoto();
      else if (e.key === 'ArrowLeft') prevPhoto();
      else if (e.key === 'Escape') closeDetail();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [closeDetail, nextPhoto, prevPhoto, selectedHome]);

  useEffect(() => {
    setActivePhotoIndex(0);
  }, [activeCategory]);

  // Lead capture handlers
  const openLeadForm = useCallback((home, type) => {
    setLeadFormHome(home);
    setLeadFormType(type);
    setShowLeadForm(true);
    trackEvent('lead_form_opened', { home: home.model_name, type });
  }, []);

  useEffect(() => {
    if (legacyRouteHandledRef.current || loading || homes.length === 0) return;
    const intent = getLegacyInventoryIntent(window.location.pathname);
    if (!intent) return;
    const home = resolveLegacyHome(homes, intent.id);
    if (!home) return;

    legacyRouteHandledRef.current = true;
    setSearchQuery('');
    openDetail(home);
    if (intent.intent === 'quote') {
      openLeadForm(home, 'price');
    }
  }, [homes, loading, openDetail, openLeadForm]);

  // --- RENDER ---

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--cp-bg)]">
        {/* Skeleton hero */}
        <div className="relative overflow-hidden border-b border-[var(--cp-border)] bg-[var(--cp-bg-2)] px-6 pb-10 pt-12 text-[var(--cp-text)]">
          <div className="max-w-3xl mx-auto text-center relative z-[1]">
            <Skeleton className="mx-auto mb-3" width="60%" height={32} />
            <Skeleton className="mx-auto mb-6" width="80%" height={16} />
            <Skeleton className="mx-auto rounded-xl" width="100%" height={48} />
          </div>
        </div>
        {/* Skeleton cards */}
        <div className="grid gap-6 p-6 max-w-7xl mx-auto"
             style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <EmptyState
        className="min-h-[60vh]"
        icon={<AlertCircle size={40} />}
        iconWrapClassName="bg-red-50 text-red-400"
        title="Unable to load inventory"
        message={error}
        action={
          <button onClick={fetchInventory} className="btn-primary active:scale-95">
            Try Again
          </button>
        }
        secondary={
          <a href={`tel:${BUSINESS_PHONE_RAW}`} className="text-sm text-blue-600 hover:underline">
            Or call us at {BUSINESS_PHONE}
          </a>
        }
      />
    );
  }

  // Stats for hero
  const newCount = homes.filter(h => getAvailabilityKind(h) === 'available_now').length;
  const preOwnedCount = homes.filter(h => getAvailabilityKind(h) === 'pre_owned').length;
  const orderableCount = homes.filter(h => getAvailabilityKind(h) === 'orderable_floorplan').length;
  const tourCount = homes.filter(h => h.matterport_id).length;
  const heroHome = homes.find(home => getHomeImage(home) && getAvailabilityKind(home) !== 'orderable_floorplan') || homes.find(home => getHomeImage(home)) || homes[0];
  const heroImage = getHomeImage(heroHome);

  return (
    <div className="min-h-screen bg-[var(--cp-bg)] text-[var(--cp-text)]">
      {/* Hero Section */}
      <section className="relative isolate overflow-hidden border-b border-[var(--cp-border)] bg-[var(--cp-bg)]">
        {heroImage && (
          <img
            src={heroImage}
            alt=""
            aria-hidden="true"
            className="absolute inset-0 -z-10 h-full w-full object-cover opacity-35"
          />
        )}
        <div className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,rgba(5,6,8,0.96),rgba(5,6,8,0.74),rgba(5,6,8,0.9))]" />
        <div className="mx-auto grid max-w-7xl gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[1.1fr_0.9fr] lg:px-8 lg:py-10">
          <div className="flex min-h-[520px] flex-col justify-center">
            <div className="mb-4 inline-flex w-fit items-center gap-2 rounded-md border border-white/30 bg-black/45 px-3 py-1.5 font-mono text-xs font-semibold uppercase text-white shadow-sm">
              <span className="cp-blink h-2 w-2 rounded-full bg-white" />
              Live Inventory + Orderable Floorplans
            </div>
            <h1 className="max-w-3xl text-4xl font-extrabold leading-tight tracking-normal text-white sm:text-5xl">
              Mobile Homes For Sale in Huffman, TX
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-relaxed text-white/90">
              Browse homes available now plus manufacturer floorplans Texas Home Outlet can custom order, with photos, floorplans, 3D tours, and quote requests in one place.
            </p>

            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              <InventoryMetric icon={CheckCircle2} label="Available Now" value={newCount} tone="blue" />
              <InventoryMetric icon={Home} label="Orderable Plans" value={orderableCount} />
              <InventoryMetric icon={Box} label="3D Tours" value={tourCount} tone="warn" />
            </div>

            {/* Search Bar */}
            <div className="mt-6 max-w-2xl rounded-lg border border-[var(--cp-border-light)] bg-[var(--cp-panel)] p-2 shadow-[0_18px_50px_rgba(0,0,0,0.32)]">
              <div className="flex items-center gap-2 rounded-md bg-[var(--cp-bg-2)] px-4 py-3">
                <Search size={20} className="flex-shrink-0 text-[var(--cp-accent)]" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search by model, manufacturer, beds, baths, type..."
                  className="min-w-0 flex-1 bg-transparent text-base text-[var(--cp-text)] outline-none placeholder:text-[var(--cp-faint)]"
                />
                {searchQuery && (
                  <button onClick={() => setSearchQuery('')} className="flex-shrink-0 text-[var(--cp-muted)] hover:text-[var(--cp-accent)]">
                    <X size={18} />
                  </button>
                )}
              </div>
            </div>

            <div className="mt-5 flex flex-wrap gap-3">
              {onAskTex && (
                <button
                  onClick={() => onAskTex("I'm looking for a new home. What do you recommend?")}
                  className="inline-flex items-center gap-2 rounded-md bg-[var(--cp-accent)] px-5 py-3 text-sm font-bold text-[var(--cp-bg)] transition hover:bg-[var(--cp-accent-hot)]"
                >
                  <MessageCircle size={18} /> Talk to Tex
                </button>
              )}
              <a
                href={`tel:${BUSINESS_PHONE_RAW}`}
                className="inline-flex items-center gap-2 rounded-md border border-[var(--cp-border-light)] bg-[var(--cp-panel)] px-5 py-3 text-sm font-semibold text-[var(--cp-text)] transition hover:border-[var(--cp-secondary)] hover:text-[var(--cp-secondary)]"
              >
                <Phone size={18} /> Call {BUSINESS_PHONE}
              </a>
            </div>
            <div className="mt-6 flex flex-wrap items-center gap-3 text-xs font-medium text-white/80">
              <span className="inline-flex items-center gap-1.5"><MapPin size={14} /> {BUSINESS_ADDRESS}, {BUSINESS_CITY}</span>
              <span className="hidden h-1 w-1 rounded-full bg-white/55 sm:inline-block" />
              <span>{BUSINESS_HOURS}</span>
              <span className="hidden h-1 w-1 rounded-full bg-white/55 sm:inline-block" />
              <span>{preOwnedCount} pre-owned homes and {orderableCount} orderable floorplans visible</span>
            </div>
          </div>

          <div className="flex items-center">
            <FeaturedHomeSpotlight
              home={heroHome}
              onClick={() => heroHome && openDetail(heroHome)}
              onScheduleTour={() => heroHome && openLeadForm(heroHome, 'tour')}
            />
          </div>
        </div>
      </section>

      <AdminInventoryPanel enabled={adminAuthed} />

      {/* Toolbar */}
      <div className="flex flex-col items-center gap-3 border-b border-[var(--cp-border)] bg-[var(--cp-bg-2)] px-4 pb-4 pt-4">
        <div className="flex items-center gap-3 justify-center flex-wrap">
          <span className="font-mono text-sm text-[var(--cp-muted)]">
            {sortedHomes.length} home{sortedHomes.length !== 1 ? 's' : ''}
          </span>

          {/* Quick status toggles */}
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={() => setFilters(f => ({ ...f, status: '' }))}
              className={`${PILL_BTN} ${filters.status === '' ? 'bg-[var(--cp-accent)] text-[var(--cp-bg)]' : 'bg-[var(--cp-panel)] text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:border-[var(--cp-border-light)]'}`}
            >All</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'available_now' }))}
              className={`${PILL_BTN} ${filters.status === 'available_now' ? 'bg-[var(--cp-accent)] text-[var(--cp-bg)]' : 'bg-[var(--cp-panel)] text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:border-[var(--cp-border-light)]'}`}
            >Available Now ({newCount})</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'orderable_floorplan' }))}
              className={`${PILL_BTN} ${filters.status === 'orderable_floorplan' ? 'bg-[var(--cp-accent)] text-[var(--cp-bg)]' : 'bg-[var(--cp-panel)] text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:border-[var(--cp-border-light)]'}`}
            >Orderable ({orderableCount})</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'pre_owned' }))}
              className={`${PILL_BTN} ${filters.status === 'pre_owned' ? 'bg-[var(--cp-accent)] text-[var(--cp-bg)]' : 'bg-[var(--cp-panel)] text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:border-[var(--cp-border-light)]'}`}
            >Pre-Owned ({preOwnedCount})</button>
          </div>
        </div>

        <div className="flex items-center gap-2 justify-center flex-wrap">
          {/* Sort dropdown */}
          <div className="flex items-center gap-1 text-[var(--cp-muted)]">
            <ArrowUpDown size={14} />
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className={CHIP_INPUT}
            >
              {SORT_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`inline-flex items-center gap-2 ${PILL_BTN} ${hasActiveFilters || showFilters ? 'bg-[var(--cp-secondary)] text-[var(--cp-bg)]' : 'bg-[var(--cp-panel)] text-[var(--cp-muted)] border-[var(--cp-border)] hover:border-[var(--cp-secondary)] hover:text-[var(--cp-secondary)]'}`}
          >
            <SlidersHorizontal size={16} />
            Filters
            {hasActiveFilters && (
              <span className="bg-white text-blue-600 text-xs font-semibold rounded-full px-2 py-0.5 ml-1">
                {Object.values(filters).filter(v => v !== '').length + (searchQuery ? 1 : 0)}
              </span>
            )}
          </button>
          {hasActiveFilters && (
            <button onClick={clearFilters} className="rounded-md border border-[var(--cp-border)] bg-[var(--cp-panel)] px-4 py-2 text-sm text-[var(--cp-muted)] transition hover:text-[var(--cp-text)]">
              Clear All
            </button>
          )}
        </div>
      </div>

      {/* Expandable Filter Panel */}
      {showFilters && (
        <div className="max-w-7xl mx-auto px-4 pb-4">
          <Card className="!bg-[var(--cp-panel)] !border-[var(--cp-border)] shadow-sm">
            <div className="grid gap-4"
                 style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-[var(--cp-muted)] uppercase tracking-wide flex items-center gap-1">
                  <Bed size={14} /> Bedrooms
                </label>
                <select className={CHIP_INPUT} value={filters.beds} onChange={e => setFilters(f => ({ ...f, beds: e.target.value }))}>
                  <option value="">Any</option>
                  <option value="1">1+</option>
                  <option value="2">2+</option>
                  <option value="3">3+</option>
                  <option value="4">4+</option>
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-[var(--cp-muted)] uppercase tracking-wide flex items-center gap-1">
                  <Bath size={14} /> Bathrooms
                </label>
                <select className={CHIP_INPUT} value={filters.baths} onChange={e => setFilters(f => ({ ...f, baths: e.target.value }))}>
                  <option value="">Any</option>
                  <option value="1">1+</option>
                  <option value="2">2+</option>
                  <option value="3">3+</option>
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-[var(--cp-muted)] uppercase tracking-wide flex items-center gap-1">
                  <Home size={14} /> Type
                </label>
                <select className={CHIP_INPUT} value={filters.classification} onChange={e => setFilters(f => ({ ...f, classification: e.target.value }))}>
                  <option value="">Any</option>
                  <option value="Single Wide">Single Wide</option>
                  <option value="Double Wide">Double Wide</option>
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-[var(--cp-muted)] uppercase tracking-wide">Min Price</label>
                <input
                  className={CHIP_INPUT}
                  type="number"
                  placeholder="$0"
                  value={filters.minPrice}
                  onChange={e => setFilters(f => ({ ...f, minPrice: e.target.value }))}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-[var(--cp-muted)] uppercase tracking-wide">Max Price</label>
                <input
                  className={CHIP_INPUT}
                  type="number"
                  placeholder="No max"
                  value={filters.maxPrice}
                  onChange={e => setFilters(f => ({ ...f, maxPrice: e.target.value }))}
                />
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Home Cards Grid */}
      <div className="grid gap-6 px-4 pb-12 pt-6 sm:px-6 max-w-7xl mx-auto"
           style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
        {sortedHomes.map((home, idx) => (
          <HomeCard
            key={home.id || idx}
            home={home}
            onClick={() => openDetail(home)}
            onScheduleTour={() => openLeadForm(home, 'tour')}
          />
        ))}
      </div>

      {sortedHomes.length === 0 && !loading && (
        <EmptyState
          icon={<Search size={36} />}
          title="No homes match your search"
          message="Try adjusting your filters or search terms"
          action={
            <button onClick={clearFilters} className="btn-primary text-sm active:scale-95">
              Clear All Filters
            </button>
          }
        />
      )}

      {/* Detail Modal */}
      {selectedHome && (
        <HomeDetailModal
          home={selectedHome}
          photos={photos}
          activePhotoIndex={activePhotoIndex}
          activeCategory={activeCategory}
          showTour={showTour}
          onClose={closeDetail}
          onPrevPhoto={prevPhoto}
          onNextPhoto={nextPhoto}
          onSetPhotoIndex={(idx) => {
            setActivePhotoIndex(idx);
            trackEvent('photo_clicked', { home: selectedHome.model_name, index: idx });
          }}
          onSetCategory={(cat) => setActiveCategory(cat)}
          onToggleTour={() => {
            setShowTour(!showTour);
            if (!showTour) trackEvent('tour_opened', { home: selectedHome.model_name });
          }}
          onAskTex={onAskTex}
          onScheduleTour={() => openLeadForm(selectedHome, 'tour')}
          onGetPrice={() => openLeadForm(selectedHome, 'price')}
          onCreateAd={onCreateAd}
          allHomes={homes}
          onSelectSimilar={setSelectedHome}
        />
      )}

      {/* Lead Capture Modal */}
      {showLeadForm && leadFormHome && (
        <LeadCaptureForm
          home={leadFormHome}
          type={leadFormType}
          onClose={() => { setShowLeadForm(false); setLeadFormHome(null); }}
        />
      )}
    </div>
  );
}


// ─── Home Card Component ───
function HomeCard({ home, onClick, onScheduleTour }) {
  // image_url is guaranteed non-floorplan after PR #43's classifier;
  // real_photos[0] is also non-floorplan (exteriors are listed first).
  // We deliberately do NOT fall back to floor_plan_url here — floorplans
  // belong in the dedicated Floorplan tab, not as the card hero.
  const floorplanUrls = getFloorplanUrls(home);
  const galleryPhotos = getListingPhotos(home);
  const photoCount = galleryPhotos.length;
  const heroImage = galleryPhotos[0] || '';
  const [heroLoadState, setHeroLoadState] = useState(heroImage ? 'loading' : 'empty');
  const hasFloorplanOnly = !heroImage && (
    home.media_quality?.status === 'floorplan_only' || floorplanUrls.length > 0
  );
  const hasTour = !!home.matterport_id;
  const specs = home.specs || {};
  const categories = home.image_categories || {};
  const quoteUrl = getLegacyQuoteUrl(home);

  useEffect(() => {
    setHeroLoadState(heroImage ? 'loading' : 'empty');
    if (!heroImage) return undefined;
    const timeout = window.setTimeout(() => {
      setHeroLoadState((state) => (state === 'loaded' ? state : 'failed'));
    }, 4500);
    return () => window.clearTimeout(timeout);
  }, [heroImage]);

  return (
    <Card padded={false} hover className="group !bg-[var(--cp-panel)] !border-[var(--cp-border)]">
      {/* Image — click opens detail */}
      <div
        className="relative h-[220px] overflow-hidden bg-[var(--cp-bg-2)] cursor-pointer"
        onClick={onClick}
      >
        {heroImage && heroLoadState !== 'failed' ? (
          <img
            src={heroImage}
            alt={home.model_name}
            className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
            loading="lazy"
            decoding="async"
            onLoad={() => setHeroLoadState('loaded')}
            onError={() => setHeroLoadState('failed')}
          />
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center gap-2 bg-[var(--cp-bg-2)] border-2 border-dashed border-[var(--cp-border-light)] m-0">
            <Camera size={32} className="text-[var(--cp-muted)]" />
            <span className="text-sm font-semibold text-[var(--cp-text-secondary)]">Photos Coming Soon</span>
            {hasFloorplanOnly && (
              <span className="text-xs font-semibold text-[var(--cp-accent)] px-2 py-1 bg-[var(--cp-accent-dim)] rounded-md">
                Floorplan Available
              </span>
            )}
            {heroImage && heroLoadState === 'failed' && (
              <span className="text-xs font-semibold text-[var(--cp-muted)]">
                Photo Unavailable
              </span>
            )}
          </div>
        )}

        {/* Status badge — top-left */}
        <div className="absolute top-3 left-3 flex flex-col gap-2">
          <StatusBadge status={home.status} kind="home" size="md" />
        </div>

        {/* Bottom-right badges */}
        <div className="absolute bottom-3 right-3 flex gap-2">
          {photoCount > 1 && (
            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs text-white bg-black/60">
              <Camera size={12} /> {photoCount}
            </span>
          )}
          {hasTour && (
            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs text-white bg-black/70 font-semibold">
              <Box size={12} /> 3D Tour
            </span>
          )}
        </div>
      </div>

      {/* Info */}
      <div className="p-5">
        <h3
          className="text-lg font-bold text-[var(--cp-text)] leading-tight cursor-pointer"
          onClick={onClick}
        >
          {home.model_name}
        </h3>
        <p className="text-sm text-[var(--cp-muted)] mt-1">{home.manufacturer || 'New Vision Manufacturing'}</p>

        <div className="flex gap-4 my-4 py-3 border-t border-b border-[var(--cp-border)] text-sm text-[var(--cp-text-secondary)]">
          {specs.beds && (
            <span className="inline-flex items-center gap-1.5"><Bed size={14} /> {specs.beds} Bed</span>
          )}
          {specs.baths && (
            <span className="inline-flex items-center gap-1.5"><Bath size={14} /> {specs.baths} Bath</span>
          )}
          {specs.sq_ft && (
            <span className="inline-flex items-center gap-1.5"><Maximize2 size={14} /> {specs.sq_ft.toLocaleString()} sqft</span>
          )}
        </div>

        {/* Room category badges */}
        {Object.keys(categories).length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {categories.kitchen && (
              <span className="inline-block text-[0.7rem] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide bg-amber-100 text-amber-800">
                Kitchen
              </span>
            )}
            {categories.bedroom && (
              <span className="inline-block text-[0.7rem] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide bg-blue-100 text-blue-800">
                Bedroom
              </span>
            )}
            {categories.bathroom && (
              <span className="inline-block text-[0.7rem] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide bg-emerald-100 text-emerald-800">
                Bathroom
              </span>
            )}
          </div>
        )}

        <div className="text-xl font-bold text-[var(--cp-accent)] mb-3">
          {home.display_price && home.display_price !== 'Call for Price'
            ? home.display_price
            : <span className="text-[var(--cp-muted)] text-base font-medium">Call for Price</span>
          }
        </div>

        {/* Dual action buttons */}
        <div className="flex gap-2">
          <button
            className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 rounded-md text-sm font-medium bg-[var(--cp-bg-2)] text-[var(--cp-text-secondary)] border border-[var(--cp-border)] hover:border-[var(--cp-secondary)] hover:text-[var(--cp-secondary)] transition"
            onClick={onClick}
          >
            <Eye size={16} /> View Details
          </button>
          {quoteUrl ? (
            <a
              href={quoteUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md bg-[var(--cp-accent)] py-2.5 text-sm font-medium text-[var(--cp-bg)] no-underline transition hover:bg-[var(--cp-accent-hot)]"
            >
              <DollarSign size={16} /> Price Quote
            </a>
          ) : (
            <button
              className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 rounded-md text-sm font-medium bg-[var(--cp-accent)] text-[var(--cp-bg)] hover:bg-[var(--cp-accent-hot)] transition"
              onClick={(e) => { e.stopPropagation(); onScheduleTour(); }}
            >
              <Calendar size={16} /> Schedule Tour
            </button>
          )}
        </div>
      </div>
    </Card>
  );
}


// ─── Home Detail Modal ───
function HomeDetailModal({
  home, photos, activePhotoIndex, activeCategory, showTour,
  onClose, onPrevPhoto, onNextPhoto, onSetPhotoIndex, onSetCategory,
  onToggleTour, onAskTex, onScheduleTour, onGetPrice, onCreateAd,
  allHomes, onSelectSimilar
}) {
  const specs = home.specs || {};
  const categories = home.image_categories || {};
  const categoryKeys = Object.keys(categories);
  const hasTour = !!home.matterport_id;
  // After PR #43 the canonical field is `floorplan_url`. Fall back to the
  // legacy `floor_plan_url` spelling for older Firestore docs.
  const floorplan = home.floorplan_url || home.floor_plan_url || '';
  const isCallForPrice = !home.display_price || home.display_price === 'Call for Price';
  const quoteUrl = getLegacyQuoteUrl(home);

  const modalRef = useRef(null);
  useFocusTrap(modalRef);

  // Floorplan view is a peer of the Photos / 3D Tour view inside the
  // gallery area. State is local to the modal because the parent already
  // owns Photos<->Tour switching via showTour, and floorplan is purely a
  // detail-modal concern.
  const [showFloorplan, setShowFloorplan] = useState(() => photos.length === 0 && !!floorplan);
  useEffect(() => {
    setShowFloorplan(photos.length === 0 && !!floorplan);
  }, [floorplan, home.id, home.model_name, photos.length]);
  // If the user toggles into 3D Tour, reset the floorplan view so the
  // gallery area only renders one thing at a time.
  useEffect(() => {
    if (showTour && showFloorplan) setShowFloorplan(false);
  }, [showTour, showFloorplan]);

  // Touch swipe for mobile
  const touchStart = useRef(null);
  const handleTouchStart = (e) => { touchStart.current = e.touches[0].clientX; };
  const handleTouchEnd = (e) => {
    if (touchStart.current === null) return;
    const diff = touchStart.current - e.changedTouches[0].clientX;
    if (Math.abs(diff) > 50) {
      if (diff > 0) onNextPhoto();
      else onPrevPhoto();
    }
    touchStart.current = null;
  };

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/75 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        ref={modalRef}
        className="relative bg-white rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 z-10 w-10 h-10 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center transition"
        >
          <X size={24} />
        </button>

        {/* Photo Gallery Section */}
        <div className="bg-slate-800 relative">
          {showTour && hasTour ? (
            <div className="h-[400px]">
              <iframe
                src={`${MATTERPORT_BASE}${home.matterport_id}&play=1`}
                title={`${home.model_name} 3D Tour`}
                className="w-full h-full border-0"
                allowFullScreen
              />
            </div>
          ) : showFloorplan ? (
            <div className="relative h-[400px] bg-slate-50 flex items-center justify-center overflow-hidden">
              {floorplan ? (
                <>
                  <img
                    src={floorplan}
                    alt={`${home.model_name} floorplan`}
                    className="max-w-full max-h-full object-contain bg-white"
                    onError={(e) => { e.target.classList.add('hidden'); }}
                  />
                  <a
                    href={floorplan}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="absolute bottom-4 right-4 px-3.5 py-2 bg-slate-900/85 hover:bg-slate-900 text-white rounded-full text-[13px] font-medium transition-colors"
                  >
                    View larger
                  </a>
                </>
              ) : (
                <div className="flex flex-col items-center justify-center gap-3 text-slate-400">
                  <Grid3X3 size={48} className="text-slate-200" />
                  <p>Floorplan unavailable</p>
                </div>
              )}
            </div>
          ) : photos.length > 0 ? (
            <div
              className="relative h-[400px] flex items-center justify-center"
              onTouchStart={handleTouchStart}
              onTouchEnd={handleTouchEnd}
            >
              <img
                src={photos[activePhotoIndex]}
                alt={`${home.model_name} photo ${activePhotoIndex + 1}`}
                className="max-w-full max-h-full object-contain"
                onError={(e) => { e.target.classList.add('opacity-0'); }}
              />
              {photos.length > 1 && (
                <>
                  <button
                    onClick={onPrevPhoto}
                    aria-label="Previous photo"
                    className="absolute left-4 top-1/2 -translate-y-1/2 w-11 h-11 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center transition"
                  >
                    <ChevronLeft size={24} />
                  </button>
                  <button
                    onClick={onNextPhoto}
                    aria-label="Next photo"
                    className="absolute right-4 top-1/2 -translate-y-1/2 w-11 h-11 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center transition"
                  >
                    <ChevronRight size={24} />
                  </button>
                  <span className="absolute bottom-4 right-4 px-3 py-1 rounded-full text-sm text-white bg-black/60">
                    {activePhotoIndex + 1} / {photos.length}
                  </span>
                </>
              )}
            </div>
          ) : (
            <div className="h-[400px] flex flex-col items-center justify-center gap-4 text-gray-400">
              <Home size={48} className="text-gray-300" />
              <p>No photos available</p>
            </div>
          )}

          {/* Category Tabs + Thumbnail Strip */}
          <div className="bg-slate-900 px-3 py-3">
            <div className="flex gap-2 mb-3 flex-wrap">
              <button
                className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition border-0 ${!showTour && !showFloorplan ? 'bg-blue-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/20 hover:text-white'}`}
                onClick={() => { if (showTour) onToggleTour(); setShowFloorplan(false); }}
              >
                <Camera size={14} /> Photos ({photos.length})
              </button>
              {hasTour && (
                <button
                  className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition border-0 ${showTour ? 'bg-blue-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/20 hover:text-white'}`}
                  onClick={() => { setShowFloorplan(false); if (!showTour) onToggleTour(); }}
                >
                  <Box size={14} /> 3D Tour
                </button>
              )}
              {floorplan && (
                <button
                  type="button"
                  className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition border-0 ${showFloorplan ? 'bg-blue-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/20 hover:text-white'}`}
                  onClick={() => { if (showTour) onToggleTour(); setShowFloorplan(true); }}
                >
                  <Grid3X3 size={14} /> Floor Plan
                </button>
              )}
            </div>

            {categoryKeys.length > 0 && !showTour && !showFloorplan && (
              <div className="flex gap-2 mb-3 flex-wrap">
                <button
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${activeCategory === 'all' ? 'bg-blue-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/20 hover:text-white'}`}
                  onClick={() => onSetCategory('all')}
                >All</button>
                {categoryKeys.map(cat => (
                  <button
                    key={cat}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${activeCategory === cat ? 'bg-blue-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/20 hover:text-white'}`}
                    onClick={() => onSetCategory(cat)}
                  >
                    {cat.charAt(0).toUpperCase() + cat.slice(1)} ({categories[cat].length})
                  </button>
                ))}
              </div>
            )}

            {!showTour && !showFloorplan && photos.length > 1 && (
              <div className="flex gap-2 overflow-x-auto pb-1">
                {photos.map((photo, idx) => (
                  <button
                    key={idx}
                    className={`flex-shrink-0 w-20 h-[60px] rounded-md overflow-hidden border-2 transition ${idx === activePhotoIndex ? 'border-blue-500 opacity-100' : 'border-transparent opacity-70 hover:opacity-100 hover:border-blue-500'}`}
                    onClick={() => onSetPhotoIndex(idx)}
                  >
                    <img src={photo} alt={`Thumb ${idx + 1}`} className="w-full h-full object-cover" loading="lazy" />
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Info Section */}
        <div className="p-6 overflow-y-auto flex-1">
          <div className="flex items-start justify-between gap-4 mb-5">
            <div>
              <h2 className="text-2xl font-bold text-slate-800">{home.model_name}</h2>
              <p className="text-sm text-gray-500 mt-0.5">{home.manufacturer || 'New Vision Manufacturing'}</p>
            </div>
            <StatusBadge status={home.status} kind="home" size="md" />
          </div>

          {/* Specs Row */}
          <div className="flex gap-6 mb-5 flex-wrap">
            {specs.beds && (
              <div className="flex items-center gap-2 text-slate-600">
                <Bed size={20} />
                <span className="font-bold text-slate-800">{specs.beds}</span>
                <span className="text-sm">Beds</span>
              </div>
            )}
            {specs.baths && (
              <div className="flex items-center gap-2 text-slate-600">
                <Bath size={20} />
                <span className="font-bold text-slate-800">{specs.baths}</span>
                <span className="text-sm">Baths</span>
              </div>
            )}
            {specs.sq_ft && (
              <div className="flex items-center gap-2 text-slate-600">
                <Maximize2 size={20} />
                <span className="font-bold text-slate-800">{specs.sq_ft.toLocaleString()}</span>
                <span className="text-sm">Sq Ft</span>
              </div>
            )}
            {specs.dimensions && (
              <div className="flex items-center gap-2 text-slate-600">
                <Grid3X3 size={20} />
                <span className="font-bold text-slate-800">{specs.dimensions}</span>
                <span className="text-sm">Dimensions</span>
              </div>
            )}
          </div>

          {/* Price */}
          <div className="flex items-center gap-4 mb-5 flex-wrap">
            <span className="text-3xl font-bold text-green-600">
              {!isCallForPrice ? home.display_price : 'Call for Price'}
            </span>
            {isCallForPrice && quoteUrl ? (
              <a
                href={quoteUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2.5 font-medium text-white no-underline transition hover:bg-amber-600"
              >
                <DollarSign size={16} /> Get Price Quote
              </a>
            ) : isCallForPrice && (
              <button
                className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium text-white bg-amber-500 hover:bg-amber-600 transition"
                onClick={onGetPrice}
              >
                <DollarSign size={16} /> Get Price Quote
              </button>
            )}
          </div>

          {/* Features */}
          {home.features && home.features.length > 0 && (
            <div className="mb-5">
              <h4 className="text-base font-semibold text-slate-800 mb-3">Features</h4>
              <ul className="grid gap-2 list-none"
                  style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
                {home.features.map((f, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm text-slate-600">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" aria-hidden="true" />
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex gap-3 mb-4 flex-col sm:flex-row">
            <button
              className="flex-1 inline-flex items-center justify-center gap-2 py-3.5 rounded-lg font-semibold text-sm bg-blue-600 text-white hover:bg-blue-700 transition"
              onClick={() => {
                onClose();
                onAskTex(`Tell me more about the ${home.model_name}. What are its key features, pricing, and availability?`);
              }}
            >
              <MessageCircle size={18} /> Ask Tex About This Home
            </button>
            <button
              className="flex-1 inline-flex items-center justify-center gap-2 py-3.5 rounded-lg font-semibold text-sm bg-emerald-500 text-white hover:bg-emerald-600 transition"
              onClick={onScheduleTour}
            >
              <Calendar size={18} /> Schedule a Tour
            </button>
            {quoteUrl && (
              <a
                href={quoteUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--cp-accent)] py-3.5 text-sm font-semibold text-[var(--cp-bg)] no-underline transition hover:bg-[var(--cp-accent-hot)]"
              >
                <DollarSign size={18} /> Price Quote
              </a>
            )}
          </div>

          <div className="flex gap-3 mb-4 flex-col sm:flex-row">
            <a
              href={`tel:${BUSINESS_PHONE_RAW}`}
              className="flex-1 inline-flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium bg-white text-slate-600 border-2 border-gray-200 hover:border-emerald-500 hover:text-emerald-500 transition no-underline"
            >
              <Phone size={16} /> Call {BUSINESS_PHONE}
            </a>
            {onCreateAd && (
              <button
                className="flex-1 inline-flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium bg-white text-slate-600 border-2 border-gray-200 hover:border-violet-500 hover:text-violet-500 transition"
                onClick={() => {
                  onClose();
                  onCreateAd(home.model_name);
                }}
              >
                <Video size={16} /> Create Ad
              </button>
            )}
          </div>

          {/* Location */}
          <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-slate-50 text-sm text-gray-500">
            <MapPin size={14} />
            <span>{BUSINESS_ADDRESS}, {BUSINESS_CITY} — {BUSINESS_HOURS}</span>
          </div>

          {/* Similar Homes */}
          <SimilarHomes
            currentHome={home}
            allHomes={allHomes}
            onSelectHome={(newHome) => {
              onSelectSimilar(newHome);
              onSetPhotoIndex(0);
            }}
          />
        </div>
      </div>
    </div>
  );
}

// ─── Similar Homes Component ───
function SimilarHomes({ currentHome, allHomes, onSelectHome }) {
  if (!currentHome || !allHomes?.length) return null;

  const currentSpecs = currentHome.specs || {};

  // Calculate similarity score
  const scored = allHomes
    .filter(h => h.id !== currentHome.id)
    .map(h => {
      const specs = h.specs || {};
      let score = 0;

      // Same manufacturer
      if (h.manufacturer === currentHome.manufacturer) score += 3;

      // Same classification
      if (h.classification === currentHome.classification) score += 2;

      // Similar beds
      const bedDiff = Math.abs((specs.beds || 0) - (currentSpecs.beds || 0));
      if (bedDiff === 0) score += 3;
      else if (bedDiff === 1) score += 1;

      // Similar baths
      const bathDiff = Math.abs((specs.baths || 0) - (currentSpecs.baths || 0));
      if (bathDiff === 0) score += 2;
      else if (bathDiff <= 0.5) score += 1;

      // Similar sqft (within 200)
      const sqftDiff = Math.abs((specs.sq_ft || 0) - (currentSpecs.sq_ft || 0));
      if (sqftDiff <= 200) score += 2;
      else if (sqftDiff <= 400) score += 1;

      // Similar price range
      const priceA = currentHome.price_value || 0;
      const priceB = h.price_value || 0;
      if (priceA && priceB) {
        const priceDiff = Math.abs(priceA - priceB) / priceA;
        if (priceDiff <= 0.1) score += 2;
        else if (priceDiff <= 0.2) score += 1;
      }

      return { home: h, score };
    })
    .filter(item => item.score >= 4) // Only show if reasonably similar
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);

  if (scored.length === 0) return null;

  return (
    <div className="mt-6 pt-6 border-t border-gray-200">
      <h4 className="flex items-center gap-2 text-base font-semibold text-slate-800 mb-4">
        <Box size={16} /> Similar Homes
      </h4>
      <div className="grid gap-3"
           style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
        {scored.map(({ home }) => {
          const image = getHomeImage(home);
          return (
            <button
              key={home.id}
              className="flex flex-col p-3 bg-slate-50 border-2 border-transparent rounded-xl text-left transition hover:border-blue-500 hover:bg-white hover:shadow-md"
              onClick={() => onSelectHome(home)}
            >
              <div className="h-[100px] rounded-lg overflow-hidden bg-slate-200 flex items-center justify-center mb-3">
                {image ? (
                  <img src={image} alt={home.model_name} className="w-full h-full object-cover" loading="lazy" />
                ) : (
                  <Home size={24} className="text-gray-300" />
                )}
              </div>
              <div className="font-semibold text-slate-800 text-sm leading-tight mb-1">
                {home.model_name}
              </div>
              <div className="text-xs text-gray-500 mb-1">
                {home.specs?.beds}BR · {home.specs?.baths}BA · {home.specs?.sq_ft?.toLocaleString()} sqft
              </div>
              <div className="font-bold text-green-600 text-sm">
                {home.display_price || 'Call for Price'}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}


// ─── Lead Capture Form Modal ───
function LeadCaptureForm({ home, type, onClose }) {
  const [formData, setFormData] = useState({ name: '', phone: '', email: '', message: '' });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');
  const modalRef = useRef(null);
  useFocusTrap(modalRef);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!formData.name || !formData.phone) {
      setError('Name and phone number are required.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const resp = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formData.name,
          phone: formData.phone,
          email: formData.email,
          message: `${type === 'tour' ? 'Tour Request' : 'Price Quote Request'} — ${home.model_name}. ${formData.message}`.trim(),
        }),
      });
      if (!resp.ok) throw new Error('Submission failed');
      setSubmitted(true);
      trackEvent('lead_captured', { home: home.model_name, type });
    } catch {
      setError(`Something went wrong. Please call us at ${BUSINESS_PHONE}.`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[110] bg-black/75 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        ref={modalRef}
        className="relative bg-white rounded-2xl w-full max-w-md p-6"
        onClick={e => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          aria-label="Close"
          className="absolute top-3 right-3 w-8 h-8 rounded-full text-gray-400 hover:bg-slate-100 hover:text-gray-700 flex items-center justify-center transition"
        >
          <X size={20} />
        </button>

        {submitted ? (
          <div className="flex flex-col items-center text-center gap-3 py-2">
            <CheckCircle2 size={48} className="text-green-500" />
            <h3 className="text-xl font-bold text-slate-800">Thank You!</h3>
            <p className="text-sm text-gray-600">
              We received your {type === 'tour' ? 'tour request' : 'price quote request'} for the <strong>{home.model_name}</strong>. Our team will contact you shortly.
            </p>
            <button onClick={onClose} className="btn-primary mt-2">
              Done
            </button>
          </div>
        ) : (
          <>
            <div className="text-center mb-6">
              <h3 className="text-xl font-bold text-slate-800 mb-2">
                {type === 'tour' ? 'Schedule a Tour' : 'Get a Price Quote'}
              </h3>
              <p className="text-sm text-gray-500">
                {type === 'tour' ? 'Visit' : 'Get pricing for'} the <strong>{home.model_name}</strong>
                {home.specs?.beds && ` — ${home.specs.beds} Bed, ${home.specs.baths} Bath`}
                {home.specs?.sq_ft && `, ${home.specs.sq_ft.toLocaleString()} sqft`}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-gray-700">Name *</label>
                <input
                  className="px-3 py-2.5 rounded-lg border-2 border-gray-200 text-sm focus:outline-none focus:border-blue-500 transition"
                  type="text"
                  value={formData.name}
                  onChange={e => setFormData(f => ({ ...f, name: e.target.value }))}
                  placeholder="Your full name"
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-gray-700">Phone *</label>
                <input
                  className="px-3 py-2.5 rounded-lg border-2 border-gray-200 text-sm focus:outline-none focus:border-blue-500 transition"
                  type="tel"
                  value={formData.phone}
                  onChange={e => setFormData(f => ({ ...f, phone: e.target.value }))}
                  placeholder="(281) 000-0000"
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-gray-700">Email</label>
                <input
                  className="px-3 py-2.5 rounded-lg border-2 border-gray-200 text-sm focus:outline-none focus:border-blue-500 transition"
                  type="email"
                  value={formData.email}
                  onChange={e => setFormData(f => ({ ...f, email: e.target.value }))}
                  placeholder="you@email.com"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-gray-700">
                  {type === 'tour' ? 'Preferred date/time' : 'Additional details'}
                </label>
                <textarea
                  className="px-3 py-2.5 rounded-lg border-2 border-gray-200 text-sm focus:outline-none focus:border-blue-500 transition"
                  value={formData.message}
                  onChange={e => setFormData(f => ({ ...f, message: e.target.value }))}
                  placeholder={type === 'tour' ? 'e.g., Saturday morning, weekday after 5pm...' : 'Any questions or preferences...'}
                  rows={3}
                />
              </div>

              {error && (
                <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">
                  <AlertCircle size={14} /> {error}
                </div>
              )}

              <button
                type="submit"
                disabled={submitting}
                className="btn-primary w-full inline-flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {submitting ? (
                  <><Loader2 size={16} className="animate-spin" /> Submitting...</>
                ) : type === 'tour' ? (
                  <><Calendar size={16} /> Request Tour</>
                ) : (
                  <><DollarSign size={16} /> Get Quote</>
                )}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
