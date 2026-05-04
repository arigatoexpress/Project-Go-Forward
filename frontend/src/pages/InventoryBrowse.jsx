import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Search, SlidersHorizontal, X, Home, Bed, Bath, Maximize2,
  Camera, Box, ChevronLeft, ChevronRight, MapPin, Phone,
  MessageCircle, Grid3X3, Loader2, Eye, ArrowUpDown, Calendar,
  DollarSign, Video, CheckCircle2, AlertCircle, RefreshCw
} from 'lucide-react';
import { BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_ADDRESS, BUSINESS_CITY, BUSINESS_HOURS } from '../constants';
import StatusBadge from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import Card from '../components/Card';


const CDN_BASE = "https://d132mt2yijm03y.cloudfront.net";
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


export default function InventoryBrowse({ onAskTex, onCreateAd }) {
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
      <div className="min-h-screen bg-slate-50 font-sans">
        {/* Skeleton hero */}
        <div className="bg-gradient-to-br from-blue-900 via-blue-800 to-blue-600 px-6 py-16 text-center">
          <div className="max-w-3xl mx-auto space-y-4 animate-pulse">
            <div className="h-10 bg-white/20 rounded-full w-2/3 mx-auto" />
            <div className="h-5 bg-white/10 rounded-full w-4/5 mx-auto" />
            <div className="h-12 bg-white/10 rounded-2xl w-full" />
          </div>
        </div>
        {/* Skeleton cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 p-6 max-w-7xl mx-auto">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="animate-pulse shadow-none">
              <div className="h-52 bg-slate-200" />
              <div className="p-5 space-y-3">
                <div className="h-4 bg-slate-200 rounded w-2/3" />
                <div className="h-3 bg-slate-100 rounded w-1/2" />
                <div className="h-8 bg-slate-100 rounded w-1/3 mt-4" />
                <div className="flex gap-2 pt-2">
                  <div className="h-10 bg-slate-200 rounded-lg flex-1" />
                  <div className="h-10 bg-slate-200 rounded-lg flex-1" />
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] p-6 text-center">
        <EmptyState 
          icon={AlertCircle}
          title="Unable to load inventory"
          description={error}
        >
          <div className="flex flex-col sm:flex-row gap-3">
            <button 
              onClick={fetchInventory} 
              className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 active:scale-95 transition-all shadow-lg shadow-blue-200"
            >
              <RefreshCw size={18} /> Try Again
            </button>
            <a 
              href={`tel:${BUSINESS_PHONE_RAW}`} 
              className="px-6 py-2.5 bg-white text-slate-700 border border-slate-200 rounded-xl font-bold hover:bg-slate-50 transition-all"
            >
              Call {BUSINESS_PHONE}
            </a>
          </div>
        </EmptyState>
      </div>
    );
  }

  // Stats for hero
  const newCount = homes.filter(h => h.status === 'Available').length;
  const preOwnedCount = homes.filter(h => h.status === 'Pre-Owned').length;
  const tourCount = homes.filter(h => h.matterport_id).length;

  return (
    <div className="min-h-screen bg-slate-50 font-sans pb-20">
      {/* Hero Section */}
      <div className="bg-gradient-to-br from-blue-950 via-blue-900 to-blue-700 px-6 py-12 md:py-20 text-center relative overflow-hidden">
        {/* Subtle decorative circles */}
        <div className="absolute top-0 right-0 w-96 h-96 bg-white/5 rounded-full -translate-y-1/2 translate-x-1/2 blur-3xl pointer-events-none" />
        <div className="absolute bottom-0 left-0 w-64 h-64 bg-blue-400/10 rounded-full translate-y-1/2 -translate-x-1/2 blur-2xl pointer-events-none" />
        
        <div className="max-w-4xl mx-auto relative z-10">
          <h1 className="text-4xl md:text-5xl font-extrabold text-white mb-4 tracking-tight">
            Find Your Perfect Home
          </h1>
          <p className="text-lg md:text-xl text-blue-100/90 mb-8 max-w-3xl mx-auto leading-relaxed font-medium">
            Browse {homes.length} manufactured homes — {newCount} new, {preOwnedCount} pre-owned — with real photos and {tourCount} 3D virtual tours
          </p>

          {/* Search Bar */}
          <div className="max-w-2xl mx-auto relative group">
            <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-slate-400 group-focus-within:text-blue-500 transition-colors">
              <Search size={22} />
            </div>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by name, manufacturer, beds, baths, type..."
              className="w-full pl-12 pr-12 py-4 bg-white rounded-2xl shadow-2xl shadow-blue-950/20 text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-4 focus:ring-blue-500/20 transition-all border-0 text-lg"
              aria-label="Search homes"
            />
            {searchQuery && (
              <button 
                onClick={() => setSearchQuery('')} 
                className="absolute inset-y-0 right-4 flex items-center text-slate-300 hover:text-slate-600"
                aria-label="Clear search"
              >
                <X size={20} />
              </button>
            )}
          </div>

          {/* Hero CTA */}
          {onAskTex && (
            <div className="flex justify-center gap-3 mt-8">
              <button
                onClick={() => onAskTex("I'm looking for a new home. What do you recommend?")}
                className="inline-flex items-center gap-2.5 px-8 py-3.5 bg-white text-blue-800 font-bold rounded-full shadow-xl shadow-blue-950/30 hover:shadow-blue-950/40 hover:-translate-y-0.5 transition-all text-base border-2 border-blue-50/50"
              >
                <MessageCircle size={20} className="text-blue-600" /> Talk to Tex — AI Advisor
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Toolbar */}
      <div className="max-w-7xl mx-auto px-6 pt-10 pb-6 flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div className="flex flex-col sm:flex-row items-center gap-6">
          <span className="text-slate-500 font-semibold text-sm whitespace-nowrap bg-white px-4 py-2 rounded-xl shadow-sm border border-slate-100">
            {sortedHomes.length} home{sortedHomes.length !== 1 ? 's' : ''} found
          </span>

          {/* Quick status toggles */}
          <div className="flex bg-slate-200/50 p-1.5 rounded-2xl border border-slate-200 shadow-inner">
            <button
              onClick={() => setFilters(f => ({ ...f, status: '' }))}
              className={`px-5 py-2 rounded-xl text-sm font-bold transition-all ${filters.status === '' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >All</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Available' }))}
              className={`px-5 py-2 rounded-xl text-sm font-bold transition-all ${filters.status === 'Available' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >New ({newCount})</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Pre-Owned' }))}
              className={`px-5 py-2 rounded-xl text-sm font-bold transition-all ${filters.status === 'Pre-Owned' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >Used ({preOwnedCount})</button>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Sort dropdown */}
          <div className="relative group">
            <div className="absolute inset-y-0 left-3.5 flex items-center pointer-events-none text-slate-400">
              <ArrowUpDown size={15} />
            </div>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="pl-10 pr-10 py-3 bg-white border border-slate-200 rounded-xl text-sm font-bold text-slate-700 focus:outline-none focus:ring-4 focus:ring-blue-500/10 transition-all appearance-none shadow-sm min-w-[180px]"
            >
              {SORT_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-bold border transition-all shadow-sm ${showFilters || hasActiveFilters ? 'bg-blue-50 border-blue-200 text-blue-700' : 'bg-white border-slate-200 text-slate-700 hover:bg-slate-50'}`}
          >
            <SlidersHorizontal size={16} />
            Filters
            {hasActiveFilters && (
              <span className="flex items-center justify-center w-5 h-5 bg-blue-600 text-white text-[10px] rounded-full">
                {Object.values(filters).filter(v => v !== '').length + (searchQuery ? 1 : 0)}
              </span>
            )}
          </button>
          {hasActiveFilters && (
            <button 
              onClick={clearFilters} 
              className="text-sm font-bold text-slate-400 hover:text-red-500 transition-colors px-2"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Expandable Filter Panel */}
      {showFilters && (
        <div className="max-w-7xl mx-auto px-6 mb-8 animate-in fade-in slide-in-from-top-4 duration-200">
          <Card className="p-8 border-blue-100 shadow-lg shadow-blue-900/5 ring-1 ring-blue-50">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
              <div className="space-y-3">
                <label className="text-xs font-bold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <Bed size={14} /> Bedrooms
                </label>
                <select 
                  className="w-full p-3 bg-slate-50 border border-slate-100 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                  value={filters.beds} 
                  onChange={e => setFilters(f => ({ ...f, beds: e.target.value }))}
                >
                  <option value="">Any</option>
                  <option value="1">1+</option>
                  <option value="2">2+</option>
                  <option value="3">3+</option>
                  <option value="4">4+</option>
                </select>
              </div>
              <div className="space-y-3">
                <label className="text-xs font-bold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <Bath size={14} /> Bathrooms
                </label>
                <select 
                  className="w-full p-3 bg-slate-50 border border-slate-100 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                  value={filters.baths} 
                  onChange={e => setFilters(f => ({ ...f, baths: e.target.value }))}
                >
                  <option value="">Any</option>
                  <option value="1">1+</option>
                  <option value="2">2+</option>
                  <option value="3">3+</option>
                </select>
              </div>
              <div className="space-y-3">
                <label className="text-xs font-bold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <Home size={14} /> Type
                </label>
                <select 
                  className="w-full p-3 bg-slate-50 border border-slate-100 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                  value={filters.classification} 
                  onChange={e => setFilters(f => ({ ...f, classification: e.target.value }))}
                >
                  <option value="">Any</option>
                  <option value="Single Wide">Single Wide</option>
                  <option value="Double Wide">Double Wide</option>
                </select>
              </div>
              <div className="space-y-3">
                <label className="text-xs font-bold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <DollarSign size={14} /> Price Range
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    placeholder="Min"
                    className="w-full p-3 bg-slate-50 border border-slate-100 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                    value={filters.minPrice}
                    onChange={e => setFilters(f => ({ ...f, minPrice: e.target.value }))}
                  />
                  <span className="text-slate-300">—</span>
                  <input
                    type="number"
                    placeholder="Max"
                    className="w-full p-3 bg-slate-50 border border-slate-100 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                    value={filters.maxPrice}
                    onChange={e => setFilters(f => ({ ...f, maxPrice: e.target.value }))}
                  />
                </div>
              </div>
            </div>
            
            <div className="mt-8 pt-6 border-t border-slate-100 flex justify-end">
              <button 
                onClick={clearFilters}
                className="px-6 py-2.5 text-slate-500 font-bold hover:text-slate-900 transition-colors"
              >
                Reset All
              </button>
              <button 
                onClick={() => setShowFilters(false)}
                className="px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold shadow-lg shadow-blue-200 hover:bg-blue-700 transition-all ml-4"
              >
                Show {sortedHomes.length} Homes
              </button>
            </div>
          </Card>
        </div>
      )}

      {/* Home Cards Grid */}
      {sortedHomes.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8 px-6 max-w-7xl mx-auto">
          {sortedHomes.map((home, idx) => (
            <HomeCard
              key={home.id || idx}
              home={home}
              onClick={() => openDetail(home)}
              onScheduleTour={() => openLeadForm(home, 'tour')}
            />
          ))}
        </div>
      ) : (
        <div className="max-w-xl mx-auto px-6 mt-12">
          <EmptyState
            icon={Search}
            title="No matches found"
            description="We couldn't find any homes matching your current filters. Try adjusting your search or clearing the filters."
          >
            <button
              onClick={clearFilters}
              className="px-8 py-3 bg-blue-600 text-white rounded-2xl font-bold shadow-lg shadow-blue-200 hover:bg-blue-700 transition-all"
            >
              Clear All Filters
            </button>
          </EmptyState>
        </div>
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
  const floorplanUrl = home.floorplan_url || home.floor_plan_url || '';
  const galleryPhotos = (home.real_photos || home.gallery_images || [])
    .filter(p => p && p !== floorplanUrl);
  const photoCount = galleryPhotos.length;
  const heroImage = home.image_url || galleryPhotos[0] || '';
  const hasTour = !!home.matterport_id;
  const specs = home.specs || {};
  const categories = home.image_categories || {};
  const isNew = home.status === 'Available';

  const [imageError, setImageError] = useState(false);

  return (
    <Card className="group hover:-translate-y-1" onClick={onClick}>
      <div className="relative h-64 overflow-hidden bg-slate-100">
        {!imageError && heroImage ? (
          <img
            src={heroImage}
            alt={home.model_name}
            className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110"
            onError={() => setImageError(true)}
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center text-slate-300 gap-3">
            <Home size={48} strokeWidth={1} />
            <span className="text-xs font-bold uppercase tracking-widest">Image Coming Soon</span>
          </div>
        )}

        {/* Badges */}
        <div className="absolute top-4 left-4 flex flex-col gap-2">
          <StatusBadge variant={isNew ? 'new' : 'used'}>
            {isNew ? 'New' : 'Used'}
          </StatusBadge>
          {hasTour && (
            <StatusBadge variant="tour">
              <Video size={10} /> 3D Tour
            </StatusBadge>
          )}
        </div>

        {/* Photo Count */}
        {photoCount > 1 && (
          <div className="absolute bottom-4 right-4 px-2 py-1 bg-black/50 text-white text-[10px] font-bold rounded flex items-center gap-1.5 backdrop-blur-md">
            <Camera size={12} /> {photoCount}
          </div>
        )}
      </div>

      <div className="p-6">
        <div className="mb-4">
          <h3 className="text-lg font-bold text-slate-900 leading-tight mb-1 group-hover:text-blue-600 transition-colors truncate" title={home.model_name}>
            {home.model_name}
          </h3>
          <p className="text-sm font-semibold text-slate-400 uppercase tracking-wider truncate">
            {home.manufacturer || 'New Vision Manufacturing'}
          </p>
        </div>

        <div className="flex items-center justify-between py-4 border-y border-slate-100 mb-4">
          <div className="flex flex-col items-center flex-1">
            <span className="text-sm font-black text-slate-900">{specs.beds || '-'}</span>
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest text-center">Beds</span>
          </div>
          <div className="w-px h-8 bg-slate-100" />
          <div className="flex flex-col items-center flex-1">
            <span className="text-sm font-black text-slate-900">{specs.baths || '-'}</span>
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest text-center">Baths</span>
          </div>
          <div className="w-px h-8 bg-slate-100" />
          <div className="flex flex-col items-center flex-1">
            <span className="text-sm font-black text-slate-900">{specs.sq_ft?.toLocaleString() || '-'}</span>
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest text-center">Sq Ft</span>
          </div>
        </div>

        {/* Room category badges */}
        {Object.keys(categories).length > 0 && (
          <div className="flex flex-wrap gap-2 mb-6">
            {categories.kitchen && <StatusBadge variant="kitchen">Kitchen</StatusBadge>}
            {categories.bedroom && <StatusBadge variant="bedroom">Bedroom</StatusBadge>}
            {categories.bathroom && <StatusBadge variant="bathroom">Bathroom</StatusBadge>}
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={(e) => { e.stopPropagation(); onScheduleTour(); }}
            className="flex-1 py-3 bg-slate-50 text-slate-600 rounded-xl text-sm font-bold hover:bg-blue-50 hover:text-blue-600 transition-all border border-transparent hover:border-blue-100 flex items-center justify-center gap-2"
          >
            <Calendar size={16} /> Tour
          </button>
          <button className="flex-1 py-3 bg-blue-600 text-white rounded-xl text-sm font-bold shadow-lg shadow-blue-200 hover:bg-blue-700 hover:shadow-blue-300 transition-all">
            {home.display_price || 'Call for Price'}
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
  const floorplan = home.floorplan_url || home.floor_plan_url || '';
  const isCallForPrice = !home.display_price || home.display_price === 'Call for Price';

  const modalRef = useRef(null);
  useFocusTrap(modalRef);

  const [showFloorplan, setShowFloorplan] = useState(false);
  useEffect(() => {
    if (showTour && showFloorplan) setShowFloorplan(false);
  }, [showTour, showFloorplan]);

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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/90 backdrop-blur-sm animate-in fade-in duration-200" onClick={onClose} aria-hidden="true">
      <div
        className="relative w-full max-w-5xl max-h-[95vh] bg-white rounded-3xl shadow-2xl overflow-y-auto animate-in slide-in-from-bottom-8 duration-300 ring-1 ring-white/20"
        onClick={e => e.stopPropagation()}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
      >
        {/* Sticky header */}
        <div className="sticky top-0 z-30 flex items-center justify-between px-6 py-4 bg-white/90 backdrop-blur-md border-b border-slate-100">
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
            <h2 className="text-xl font-black text-slate-900 leading-tight">{home.model_name}</h2>
            <div className="flex items-center gap-3">
              <StatusBadge variant={home.status === 'Available' ? 'new' : 'used'}>
                {home.status === 'Available' ? 'New' : 'Used'}
              </StatusBadge>
              <span className="text-lg font-bold text-green-600">
                {!isCallForPrice ? home.display_price : 'Call for Price'}
              </span>
            </div>
          </div>
          <button 
            onClick={onClose} 
            className="p-2.5 bg-slate-100 text-slate-400 hover:bg-slate-200 hover:text-slate-900 rounded-full transition-all"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Gallery Section */}
        <div className="bg-slate-950 relative aspect-video sm:aspect-auto sm:h-[500px]">
          {showTour && hasTour ? (
            <div className="w-full h-full">
              <iframe
                src={`${MATTERPORT_BASE}${home.matterport_id}&play=1`}
                title={`${home.model_name} 3D Tour`}
                className="w-full h-full border-0"
                allowFullScreen
              />
            </div>
          ) : showFloorplan ? (
            <div className="w-full h-full flex items-center justify-center bg-white p-4">
              {floorplan ? (
                <>
                  <img
                    src={floorplan}
                    alt="Floorplan"
                    className="max-w-full max-h-full object-contain"
                  />
                  <a
                    href={floorplan}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="absolute bottom-6 right-6 px-6 py-2.5 bg-slate-900 text-white rounded-full text-xs font-bold uppercase tracking-widest hover:scale-105 transition-transform"
                  >
                    View Larger
                  </a>
                </>
              ) : (
                <EmptyState icon={Grid3X3} title="Floorplan coming soon" description="We are currently processing the floorplan for this model." />
              )}
            </div>
          ) : photos.length > 0 ? (
            <div
              className="w-full h-full relative group"
              onTouchStart={handleTouchStart}
              onTouchEnd={handleTouchEnd}
            >
              <img
                src={photos[activePhotoIndex]}
                alt="Property"
                className="w-full h-full object-contain"
              />
              {photos.length > 1 && (
                <>
                  <button onClick={onPrevPhoto} className="absolute left-4 top-1/2 -translate-y-1/2 p-3 bg-white/10 hover:bg-white text-white hover:text-slate-900 rounded-full backdrop-blur-md transition-all opacity-0 group-hover:opacity-100">
                    <ChevronLeft size={28} />
                  </button>
                  <button onClick={onNextPhoto} className="absolute right-4 top-1/2 -translate-y-1/2 p-3 bg-white/10 hover:bg-white text-white hover:text-slate-900 rounded-full backdrop-blur-md transition-all opacity-0 group-hover:opacity-100">
                    <ChevronRight size={28} />
                  </button>
                  <div className="absolute bottom-6 right-6 px-3 py-1.5 bg-black/40 text-white text-xs font-bold rounded-full backdrop-blur-md border border-white/10">
                    {activePhotoIndex + 1} / {photos.length}
                  </div>
                </>
              )}
            </div>
          ) : (
            <EmptyState icon={Camera} title="No Photos" description="Check back soon for interior photos." className="bg-slate-900 text-white border-0" />
          )}
        </div>

        {/* Gallery Controls */}
        <div className="px-6 py-6 bg-slate-50 border-b border-slate-100">
          <div className="flex flex-wrap gap-2 mb-6">
            <button
              className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-xs font-black uppercase tracking-widest transition-all ${!showTour && !showFloorplan ? 'bg-blue-600 text-white shadow-lg shadow-blue-200' : 'bg-white text-slate-500 hover:bg-slate-100'}`}
              onClick={() => { if (showTour) onToggleTour(); if (showFloorplan) setShowFloorplan(false); }}
            >
              <Camera size={16} /> Photos
            </button>
            {hasTour && (
              <button
                className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-xs font-black uppercase tracking-widest transition-all ${showTour ? 'bg-blue-600 text-white shadow-lg shadow-blue-200' : 'bg-white text-slate-500 hover:bg-slate-100'}`}
                onClick={() => { if (showFloorplan) setShowFloorplan(false); if (!showTour) onToggleTour(); }}
              >
                <Box size={16} /> 3D Tour
              </button>
            )}
            <button
              className={`flex items-center gap-2 px-6 py-3 rounded-2xl text-xs font-black uppercase tracking-widest transition-all ${showFloorplan ? 'bg-blue-600 text-white shadow-lg shadow-blue-200' : 'bg-white text-slate-500 hover:bg-slate-100'}`}
              onClick={() => { if (showTour) onToggleTour(); setShowFloorplan(prev => !prev); }}
            >
              <Grid3X3 size={16} /> Floorplan
            </button>
          </div>

          {!showTour && !showFloorplan && categoryKeys.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              <button
                className={`px-4 py-1.5 rounded-full text-[10px] font-bold uppercase tracking-widest transition-all ${activeCategory === 'all' ? 'bg-slate-900 text-white' : 'bg-slate-200 text-slate-500 hover:bg-slate-300'}`}
                onClick={() => onSetCategory('all')}
              >All</button>
              {categoryKeys.map(cat => (
                <button
                  key={cat}
                  className={`px-4 py-1.5 rounded-full text-[10px] font-bold uppercase tracking-widest transition-all ${activeCategory === cat ? 'bg-slate-900 text-white' : 'bg-slate-200 text-slate-500 hover:bg-slate-300'}`}
                  onClick={() => onSetCategory(cat)}
                >
                  {cat} ({categories[cat].length})
                </button>
              ))}
            </div>
          )}

          {!showTour && !showFloorplan && photos.length > 1 && (
            <div className="flex gap-2.5 overflow-x-auto pb-2 scrollbar-hide">
              {photos.map((photo, idx) => (
                <button
                  key={idx}
                  className={`flex-shrink-0 w-24 h-16 rounded-xl overflow-hidden border-2 transition-all ${idx === activePhotoIndex ? 'border-blue-600 ring-4 ring-blue-100 scale-105' : 'border-transparent opacity-60 hover:opacity-100'}`}
                  onClick={() => onSetPhotoIndex(idx)}
                >
                  <img src={photo} alt="Thumb" className="w-full h-full object-cover" loading="lazy" />
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Info Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-12 p-8 sm:p-12">
          <div className="lg:col-span-2 space-y-12">
            <section>
              <h3 className="text-xs font-black text-slate-400 uppercase tracking-[0.2em] mb-6">Key Specifications</h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
                {[
                  { icon: Bed, value: specs.beds, label: 'Bedrooms' },
                  { icon: Bath, value: specs.baths, label: 'Bathrooms' },
                  { icon: Maximize2, value: specs.sq_ft?.toLocaleString(), label: 'Sq Ft' },
                  { icon: Grid3X3, value: specs.dimensions, label: 'Dimensions' }
                ].filter(s => s.value).map((spec, i) => (
                  <div key={i} className="bg-slate-50 p-6 rounded-3xl border border-slate-100 flex flex-col items-center text-center">
                    <spec.icon size={24} className="text-blue-600 mb-3" />
                    <span className="text-xl font-black text-slate-900 mb-1">{spec.value}</span>
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{spec.label}</span>
                  </div>
                ))}
              </div>
            </section>

            {home.features?.length > 0 && (
              <section>
                <h3 className="text-xs font-black text-slate-400 uppercase tracking-[0.2em] mb-6">Top Features</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-y-4 gap-x-12">
                  {home.features.map((f, i) => (
                    <div key={i} className="flex items-center gap-3 text-slate-700 font-medium border-b border-slate-50 pb-3">
                      <div className="w-1.5 h-1.5 bg-blue-500 rounded-full" />
                      {f}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Similar Homes Section (Internal) */}
            <SimilarHomes 
              currentHome={home}
              allHomes={allHomes}
              onSelectHome={(newHome) => {
                onSelectSimilar(newHome);
                onSetPhotoIndex(0);
              }}
            />
          </div>

          <aside className="space-y-6">
            <div className="bg-blue-900 rounded-[2.5rem] p-8 text-white shadow-2xl shadow-blue-950/20 sticky top-28">
              <h3 className="text-2xl font-black mb-2">Interested?</h3>
              <p className="text-blue-100/70 text-sm mb-8 leading-relaxed">Our specialists are ready to answer your questions or schedule a walk-through.</p>
              
              <div className="space-y-4">
                <button 
                  onClick={onScheduleTour}
                  className="w-full py-4 bg-blue-600 hover:bg-blue-500 text-white rounded-2xl font-bold shadow-lg shadow-blue-950/30 transition-all flex items-center justify-center gap-2"
                >
                  <Calendar size={18} /> Schedule Tour
                </button>
                <button 
                  onClick={() => { onClose(); onAskTex(`Tell me more about the ${home.model_name}`); }}
                  className="w-full py-4 bg-white text-blue-900 rounded-2xl font-bold hover:bg-blue-50 transition-all flex items-center justify-center gap-2"
                >
                  <MessageCircle size={18} /> Chat with Tex
                </button>
                {isCallForPrice && (
                  <button onClick={onGetPrice} className="w-full py-4 bg-blue-800/50 text-white border border-white/10 rounded-2xl font-bold hover:bg-blue-800 transition-all">
                    Get Price Quote
                  </button>
                )}
                {onCreateAd && (
                  <button 
                    onClick={() => { onClose(); onCreateAd(home.model_name); }}
                    className="w-full py-4 bg-blue-950 text-blue-300 border border-blue-800 rounded-2xl font-bold hover:bg-blue-900 transition-all flex items-center justify-center gap-2"
                  >
                    <Video size={18} /> Create Social Ad
                  </button>
                )}
              </div>

              <div className="mt-8 pt-8 border-t border-white/10 space-y-4">
                <div className="flex items-center gap-3 text-blue-100/60 text-xs font-bold uppercase tracking-widest">
                  <MapPin size={14} /> Local Inventory
                </div>
                <p className="text-blue-50 text-sm font-medium">{BUSINESS_ADDRESS}<br/>{BUSINESS_CITY}</p>
                <a href={`tel:${BUSINESS_PHONE_RAW}`} className="flex items-center gap-2 text-white font-black hover:text-blue-200 transition-colors">
                  <Phone size={16} /> {BUSINESS_PHONE}
                </a>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
// ─── Similar Homes Component ───
function SimilarHomes({ currentHome, allHomes, onSelectHome }) {
  if (!currentHome || !allHomes?.length) return null;
  
  const currentSpecs = currentHome.specs || {};
  const scored = allHomes
    .filter(h => h.id !== currentHome.id)
    .map(h => {
      const specs = h.specs || {};
      let score = 0;
      if (h.manufacturer === currentHome.manufacturer) score += 3;
      if (h.classification === currentHome.classification) score += 2;
      const bedDiff = Math.abs((specs.beds || 0) - (currentSpecs.beds || 0));
      if (bedDiff === 0) score += 3;
      else if (bedDiff === 1) score += 1;
      const bathDiff = Math.abs((specs.baths || 0) - (currentSpecs.baths || 0));
      if (bathDiff === 0) score += 2;
      else if (bathDiff <= 0.5) score += 1;
      const sqftDiff = Math.abs((specs.sq_ft || 0) - (currentSpecs.sq_ft || 0));
      if (sqftDiff <= 200) score += 2;
      else if (sqftDiff <= 400) score += 1;
      return { home: h, score };
    })
    .filter(item => item.score >= 4)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
  
  if (scored.length === 0) return null;
  
  return (
    <section>
      <h3 className="text-xs font-black text-slate-400 uppercase tracking-[0.2em] mb-6 flex items-center gap-2">
        <Home size={14} /> Similar Models
      </h3>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
        {scored.map(({ home }) => (
          <button
            key={home.id}
            className="flex flex-col text-left group"
            onClick={() => onSelectHome(home)}
          >
            <div className="aspect-[4/3] rounded-2xl overflow-hidden bg-slate-100 mb-3 border border-slate-100 group-hover:border-blue-200 transition-all">
              {home.image_url ? (
                <img src={home.image_url} alt={home.model_name} className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500" />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-slate-300"><Home size={24} /></div>
              )}
            </div>
            <div className="px-1">
              <div className="text-sm font-bold text-slate-900 group-hover:text-blue-600 transition-colors">{home.model_name}</div>
              <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
                {home.specs?.beds}BR · {home.specs?.baths}BA · {home.specs?.sq_ft?.toLocaleString()} sqft
              </div>
            </div>
          </button>
        ))}
      </div>
    </section>
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
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md animate-in fade-in duration-200" onClick={onClose} aria-hidden="true">
      <div
        className="relative w-full max-w-lg bg-white rounded-[2.5rem] shadow-2xl p-8 sm:p-12 animate-in zoom-in-95 duration-300"
        onClick={e => e.stopPropagation()}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
      >
        <button 
          onClick={onClose} 
          className="absolute top-8 right-8 text-slate-300 hover:text-slate-900 transition-colors" 
          aria-label="Close"
        >
          <X size={24} />
        </button>

        {submitted ? (
          <div className="text-center py-8">
            <div className="w-20 h-20 bg-green-50 rounded-full flex items-center justify-center mx-auto mb-6 text-green-500">
              <CheckCircle2 size={40} />
            </div>
            <h3 className="text-2xl font-black text-slate-900 mb-2">Thank You!</h3>
            <p className="text-slate-500 mb-8">We've received your {type === 'tour' ? 'tour request' : 'price quote'} for the <strong>{home.model_name}</strong>. Our team will contact you shortly.</p>
            <button 
              onClick={onClose} 
              className="w-full py-4 bg-slate-900 text-white rounded-2xl font-bold hover:bg-slate-800 transition-all"
            >
              Done
            </button>
          </div>
        ) : (
          <>
            <header className="mb-8">
              <h3 className="text-2xl font-black text-slate-900 mb-2">{type === 'tour' ? 'Schedule a Tour' : 'Get a Price Quote'}</h3>
              <p className="text-slate-500 text-sm">
                For the <strong>{home.model_name}</strong>
                {home.specs?.beds && ` — ${home.specs.beds} Bed, ${home.specs.baths} Bath`}
              </p>
            </header>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest ml-1" htmlFor="lead-name">Full Name *</label>
                <input
                  id="lead-name"
                  className="w-full px-5 py-4 bg-slate-50 border border-slate-100 rounded-2xl focus:outline-none focus:ring-4 focus:ring-blue-500/10 transition-all"
                  type="text"
                  value={formData.name}
                  onChange={e => setFormData(f => ({ ...f, name: e.target.value }))}
                  placeholder="John Doe"
                  required
                />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest ml-1" htmlFor="lead-phone">Phone *</label>
                  <input
                    id="lead-phone"
                    className="w-full px-5 py-4 bg-slate-50 border border-slate-100 rounded-2xl focus:outline-none focus:ring-4 focus:ring-blue-500/10 transition-all"
                    type="tel"
                    value={formData.phone}
                    onChange={e => setFormData(f => ({ ...f, phone: e.target.value }))}
                    placeholder="(281) 000-0000"
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest ml-1" htmlFor="lead-email">Email</label>
                  <input
                    id="lead-email"
                    className="w-full px-5 py-4 bg-slate-50 border border-slate-100 rounded-2xl focus:outline-none focus:ring-4 focus:ring-blue-500/10 transition-all"
                    type="email"
                    value={formData.email}
                    onChange={e => setFormData(f => ({ ...f, email: e.target.value }))}
                    placeholder="john@example.com"
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest ml-1" htmlFor="lead-message">{type === 'tour' ? 'Preferred date/time' : 'Questions'}</label>
                <textarea
                  id="lead-message"
                  className="w-full px-5 py-4 bg-slate-50 border border-slate-100 rounded-2xl focus:outline-none focus:ring-4 focus:ring-blue-500/10 transition-all resize-none"
                  value={formData.message}
                  onChange={e => setFormData(f => ({ ...f, message: e.target.value }))}
                  placeholder={type === 'tour' ? 'e.g. This Saturday morning...' : 'Any specific questions?'}
                  rows={3}
                />
              </div>

              {error && (
                <div className="p-4 bg-red-50 text-red-600 rounded-2xl text-xs font-bold flex items-center gap-2">
                  <AlertCircle size={14} /> {error}
                </div>
              )}

              <button 
                type="submit" 
                disabled={submitting}
                className="w-full py-5 bg-blue-600 text-white rounded-2xl font-black shadow-xl shadow-blue-200 hover:bg-blue-700 hover:-translate-y-0.5 transition-all disabled:opacity-50 flex items-center justify-center gap-2 mt-4"
              >
                {submitting ? <Loader2 className="animate-spin" size={20} /> : <><Check size={20} /> Submit Request</>}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
