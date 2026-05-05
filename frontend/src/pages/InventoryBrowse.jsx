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
const HERO_BG =
  'bg-gradient-to-br from-[#1e3a5f] via-[#1e40af] to-[#3b82f6] text-white';
const PILL_BTN =
  'px-4 py-2.5 rounded-full text-sm font-medium transition border-2 border-transparent';
const CHIP_INPUT =
  'px-3 py-2 rounded-lg border-2 border-gray-200 text-sm bg-white focus:outline-none focus:border-blue-500 transition';

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
      background: 'rgba(0,0,0,0.04)', border: '1px solid rgba(0,0,0,0.08)', borderRadius: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, color: '#666' }}>
          Inventory Analytics (admin)
        </div>
        <div style={{ fontSize: 10, color: '#999' }}>cached 5m</div>
      </div>
      {loading && <div style={{ fontSize: 12, color: '#888' }}>Loading...</div>}
      {error && <div style={{ fontSize: 12, color: '#ef4444' }}>Error: {error}</div>}
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
              <div key={m.label} style={{ background: '#fff', border: '1px solid rgba(0,0,0,0.08)', borderRadius: 6, padding: '8px 12px' }}>
                <div style={{ fontSize: 10, color: '#999', textTransform: 'uppercase', letterSpacing: 0.5 }}>{m.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: m.color || '#222', marginTop: 2 }}>
                  {m.value ?? '—'}
                </div>
              </div>
            ))}
          </div>

          {data.by_manufacturer?.length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: '#666', marginBottom: 6, fontWeight: 600 }}>By Manufacturer</div>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr', fontSize: 11, color: '#666', padding: '4px 8px', borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
                <span>Manufacturer</span>
                <span style={{ textAlign: 'right' }}>Total</span>
                <span style={{ textAlign: 'right' }}>Available</span>
                <span style={{ textAlign: 'right' }}>Sold</span>
                <span style={{ textAlign: 'right' }}>Median Price</span>
              </div>
              {data.by_manufacturer.slice(0, 12).map(m => (
                <div key={m.manufacturer} style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr', fontSize: 12, padding: '4px 8px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  <span style={{ color: '#222', fontWeight: 500 }}>{m.manufacturer}</span>
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

  useEffect(() => {
    fetchInventory();
  }, []);

  const fetchInventory = async () => {
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
  };

  // Enhanced search — matches name, manufacturer, features, classification, specs
  const matchesSearch = (home, q) => {
    const query = q.toLowerCase();
    const name = (home.model_name || '').toLowerCase();
    const mfr = (home.manufacturer || '').toLowerCase();
    const classification = (home.classification || '').toLowerCase();
    const features = (home.features || []).join(' ').toLowerCase();
    const dims = (home.specs?.dimensions || '').toLowerCase();
    const beds = String(home.specs?.beds || '');
    const baths = String(home.specs?.baths || '');

    return (
      name.includes(query) ||
      mfr.includes(query) ||
      classification.includes(query) ||
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
    if (filters.status && home.status !== filters.status) return false;
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
    const photosA = (a.real_photos || a.gallery_images || []).length;
    const photosB = (b.real_photos || b.gallery_images || []).length;

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
    const floorplanUrl = home.floorplan_url || home.floor_plan_url || '';
    const rawPhotos = home.real_photos || home.gallery_images || [];

    // Hero-first ordering: image_url is guaranteed-non-floorplan after
    // PR #43, so it's safe to surface as the first gallery photo.
    const heroSequence = [];
    if (home.image_url) heroSequence.push(home.image_url);
    for (const p of rawPhotos) {
      if (p && !heroSequence.includes(p)) heroSequence.push(p);
    }

    // Defense-in-depth: even though PR #43's classifier removes the
    // floorplan from image_url and pushes it to the tail of real_photos,
    // explicitly drop the floorplan_url here so the gallery only shows
    // listing photos.
    const allPhotos = heroSequence.filter(p => p && p !== floorplanUrl);

    if (activeCategory === 'all' || !home.image_categories) return allPhotos;

    const catFiles = home.image_categories[activeCategory] || [];
    if (catFiles.length === 0) return allPhotos;

    return catFiles.map(f => {
      if (f.startsWith('http')) return f;
      const match = allPhotos.find(url => url.includes(f));
      return match || f;
    });
  }, [activeCategory]);

  const openDetail = (home) => {
    setSelectedHome(home);
    setActivePhotoIndex(0);
    setActiveCategory('all');
    setShowTour(false);
    trackEvent('home_viewed', { home: home.model_name, status: home.status });
  };

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
  const openLeadForm = (home, type) => {
    setLeadFormHome(home);
    setLeadFormType(type);
    setShowLeadForm(true);
    trackEvent('lead_form_opened', { home: home.model_name, type });
  };

  // --- RENDER ---

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50">
        {/* Skeleton hero */}
        <div className={`${HERO_BG} px-6 pt-12 pb-10 relative overflow-hidden`}>
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
  const newCount = homes.filter(h => h.status === 'Available').length;
  const preOwnedCount = homes.filter(h => h.status === 'Pre-Owned').length;
  const tourCount = homes.filter(h => h.matterport_id).length;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Hero Section */}
      <div className={`${HERO_BG} px-6 pt-12 pb-10 relative overflow-hidden`}>
        {/* Decorative radial accent — replaces .tho-browse-hero::before */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-1/2 -right-[20%] w-[400px] h-[400px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 70%)' }}
        />
        <div className="max-w-3xl mx-auto text-center relative z-[1]">
          <h1 className="text-4xl font-extrabold tracking-tight mb-3">Find Your Perfect Home</h1>
          <p className="text-lg opacity-90 mb-6 leading-relaxed">
            Browse {homes.length} manufactured homes — {newCount} new, {preOwnedCount} pre-owned — with real photos and {tourCount} 3D virtual tours
          </p>

          {/* Search Bar */}
          <div className="flex items-center gap-2 bg-white rounded-xl px-4 py-3 shadow-md max-w-2xl mx-auto">
            <Search size={20} className="text-gray-400 flex-shrink-0" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by name, manufacturer, beds, baths, type..."
              className="flex-1 outline-none text-gray-800 text-base bg-transparent"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="text-gray-400 hover:text-gray-600 flex-shrink-0">
                <X size={18} />
              </button>
            )}
          </div>

          {/* Hero CTA */}
          {onAskTex && (
            <div className="flex justify-center gap-3 mt-4">
              <button
                onClick={() => onAskTex("I'm looking for a new home. What do you recommend?")}
                className="inline-flex items-center gap-2 px-6 py-3 bg-white text-blue-700 font-bold rounded-full shadow-lg hover:shadow-xl hover:scale-105 transition-all text-sm"
              >
                <MessageCircle size={18} /> Talk to Tex — AI Home Advisor
              </button>
            </div>
          )}
        </div>
      </div>

      <AdminInventoryPanel enabled={adminAuthed} />

      {/* Toolbar */}
      <div className="flex flex-col items-center gap-3 px-4 pt-4 pb-2">
        <div className="flex items-center gap-3 justify-center flex-wrap">
          <span className="text-sm text-gray-600">
            {sortedHomes.length} home{sortedHomes.length !== 1 ? 's' : ''}
          </span>

          {/* Quick status toggles */}
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={() => setFilters(f => ({ ...f, status: '' }))}
              className={`${PILL_BTN} ${filters.status === '' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-slate-100'}`}
            >All</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Available' }))}
              className={`${PILL_BTN} ${filters.status === 'Available' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-slate-100'}`}
            >New ({newCount})</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Pre-Owned' }))}
              className={`${PILL_BTN} ${filters.status === 'Pre-Owned' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-slate-100'}`}
            >Pre-Owned ({preOwnedCount})</button>
          </div>
        </div>

        <div className="flex items-center gap-2 justify-center flex-wrap">
          {/* Sort dropdown */}
          <div className="flex items-center gap-1 text-gray-600">
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
            className={`inline-flex items-center gap-2 ${PILL_BTN} ${hasActiveFilters || showFilters ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 border-gray-200 hover:border-blue-500 hover:text-blue-600'}`}
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
            <button onClick={clearFilters} className="btn-secondary text-sm">
              Clear All
            </button>
          )}
        </div>
      </div>

      {/* Expandable Filter Panel */}
      {showFilters && (
        <div className="max-w-7xl mx-auto px-4 pb-4">
          <Card className="shadow-sm">
            <div className="grid gap-4"
                 style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1">
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
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1">
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
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1">
                  <Home size={14} /> Type
                </label>
                <select className={CHIP_INPUT} value={filters.classification} onChange={e => setFilters(f => ({ ...f, classification: e.target.value }))}>
                  <option value="">Any</option>
                  <option value="Single Wide">Single Wide</option>
                  <option value="Double Wide">Double Wide</option>
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Min Price</label>
                <input
                  className={CHIP_INPUT}
                  type="number"
                  placeholder="$0"
                  value={filters.minPrice}
                  onChange={e => setFilters(f => ({ ...f, minPrice: e.target.value }))}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Max Price</label>
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
      <div className="grid gap-6 px-6 pb-12 max-w-7xl mx-auto"
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
  const floorplanUrl = home.floorplan_url || home.floor_plan_url || '';
  const galleryPhotos = (home.real_photos || home.gallery_images || [])
    .filter(p => p && p !== floorplanUrl);
  const photoCount = galleryPhotos.length;
  const heroImage = home.image_url || galleryPhotos[0] || '';
  const hasTour = !!home.matterport_id;
  const specs = home.specs || {};
  const categories = home.image_categories || {};

  return (
    <Card padded={false} hover className="group">
      {/* Image — click opens detail */}
      <div
        className="relative h-[220px] overflow-hidden bg-slate-100 cursor-pointer"
        onClick={onClick}
      >
        {heroImage ? (
          <img
            src={heroImage}
            alt={home.model_name}
            className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
            loading="lazy"
            onError={(e) => { e.target.src = ''; e.target.classList.add('opacity-0'); }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Home size={32} className="text-gray-300" />
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
          className="text-lg font-bold text-slate-800 leading-tight cursor-pointer"
          onClick={onClick}
        >
          {home.model_name}
        </h3>
        <p className="text-sm text-gray-500 mt-1">{home.manufacturer || 'New Vision Manufacturing'}</p>

        <div className="flex gap-4 my-4 py-3 border-t border-b border-gray-200 text-sm text-gray-600">
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

        <div className="text-xl font-bold text-green-600 mb-3">
          {home.display_price && home.display_price !== 'Call for Price'
            ? home.display_price
            : <span className="text-gray-500 text-base font-medium">Call for Price</span>
          }
        </div>

        {/* Dual action buttons */}
        <div className="flex gap-2">
          <button
            className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 rounded-lg text-sm font-medium bg-white text-slate-600 border-2 border-gray-200 hover:border-blue-500 hover:text-blue-600 transition"
            onClick={onClick}
          >
            <Eye size={16} /> View Details
          </button>
          <button
            className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition"
            onClick={(e) => { e.stopPropagation(); onScheduleTour(); }}
          >
            <Calendar size={16} /> Schedule Tour
          </button>
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

  const modalRef = useRef(null);
  useFocusTrap(modalRef);

  // Floorplan view is a peer of the Photos / 3D Tour view inside the
  // gallery area. State is local to the modal because the parent already
  // owns Photos<->Tour switching via showTour, and floorplan is purely a
  // detail-modal concern.
  const [showFloorplan, setShowFloorplan] = useState(false);
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
                className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition border-0 ${!showTour ? 'bg-blue-600 text-white' : 'bg-white/10 text-slate-400 hover:bg-white/20'}`}
                onClick={() => { if (showTour) onToggleTour(); }}
              >
                <Camera size={14} /> Photos ({photos.length})
              </button>
              {hasTour && (
                <button
                  className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition border-0 ${showTour ? 'bg-blue-600 text-white' : 'bg-white/10 text-slate-400 hover:bg-white/20'}`}
                  onClick={() => { if (!showTour) onToggleTour(); }}
                >
                  <Box size={14} /> 3D Tour
                </button>
              )}
              {floorplan && (
                <a
                  href={floorplan}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition bg-white/10 text-slate-400 hover:bg-white/20"
                >
                  <Grid3X3 size={14} /> Floor Plan
                </a>
              )}
            </div>

            {categoryKeys.length > 0 && !showTour && (
              <div className="flex gap-2 mb-3 flex-wrap">
                <button
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${activeCategory === 'all' ? 'bg-blue-600 text-white' : 'bg-white/10 text-slate-400 hover:bg-white/20'}`}
                  onClick={() => onSetCategory('all')}
                >All</button>
                {categoryKeys.map(cat => (
                  <button
                    key={cat}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${activeCategory === cat ? 'bg-blue-600 text-white' : 'bg-white/10 text-slate-400 hover:bg-white/20'}`}
                    onClick={() => onSetCategory(cat)}
                  >
                    {cat.charAt(0).toUpperCase() + cat.slice(1)} ({categories[cat].length})
                  </button>
                ))}
              </div>
            )}

            {!showTour && photos.length > 1 && (
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
            {isCallForPrice && (
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
        {scored.map(({ home }) => (
          <button
            key={home.id}
            className="flex flex-col p-3 bg-slate-50 border-2 border-transparent rounded-xl text-left transition hover:border-blue-500 hover:bg-white hover:shadow-md"
            onClick={() => onSelectHome(home)}
          >
            <div className="h-[100px] rounded-lg overflow-hidden bg-slate-200 flex items-center justify-center mb-3">
              {home.image_url ? (
                <img src={home.image_url} alt={home.model_name} className="w-full h-full object-cover" loading="lazy" />
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
        ))}
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
