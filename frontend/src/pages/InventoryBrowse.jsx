import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Search, SlidersHorizontal, X, Home, Bed, Bath, Maximize2,
  Camera, Box, ChevronLeft, ChevronRight, MapPin, Phone,
  MessageCircle, Grid3X3, Loader2, Eye, ArrowUpDown, Calendar,
  DollarSign, Video, CheckCircle2, AlertCircle
} from 'lucide-react';
import { BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_ADDRESS, BUSINESS_CITY, BUSINESS_HOURS } from '../constants';
import './InventoryBrowse.css';

const CDN_BASE = "https://d132mt2yijm03y.cloudfront.net";
const MATTERPORT_BASE = "https://my.matterport.com/show/?m=";

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

  // Photo helpers for detail modal
  const getPhotosForCategory = useCallback((home) => {
    if (!home) return [];
    const allPhotos = home.real_photos || home.gallery_images || [];
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
      <div className="tho-browse">
        {/* Skeleton hero */}
        <div className="tho-browse-hero">
          <div className="tho-browse-hero-content">
            <div className="tho-skeleton" style={{ width: '60%', height: 32, margin: '0 auto 12px' }} />
            <div className="tho-skeleton" style={{ width: '80%', height: 16, margin: '0 auto 24px' }} />
            <div className="tho-skeleton" style={{ width: '100%', height: 48, borderRadius: 12 }} />
          </div>
        </div>
        {/* Skeleton cards */}
        <div className="tho-skeleton-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="tho-skeleton-card">
              <div className="tho-skeleton tho-sk-image" />
              <div className="tho-sk-body">
                <div className="tho-skeleton tho-sk-line medium" />
                <div className="tho-skeleton tho-sk-line short" />
                <div className="tho-skeleton tho-sk-price" />
                <div className="tho-sk-actions">
                  <div className="tho-skeleton tho-sk-btn" />
                  <div className="tho-skeleton tho-sk-btn" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="tho-browse-loading">
        <AlertCircle size={40} className="text-red-400 mb-3" />
        <p className="text-gray-700 font-medium mb-1">Unable to load inventory</p>
        <p className="text-gray-500 text-sm mb-4">{error}</p>
        <button onClick={fetchInventory} className="px-6 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 active:scale-95 transition">
          Try Again
        </button>
        <a href={`tel:${BUSINESS_PHONE_RAW}`} className="mt-3 text-sm text-blue-600 hover:underline">
          Or call us at {BUSINESS_PHONE}
        </a>
      </div>
    );
  }

  // Stats for hero
  const newCount = homes.filter(h => h.status === 'Available').length;
  const preOwnedCount = homes.filter(h => h.status === 'Pre-Owned').length;
  const tourCount = homes.filter(h => h.matterport_id).length;

  return (
    <div className="tho-browse">
      {/* Hero Section */}
      <div className="tho-browse-hero">
        <div className="tho-browse-hero-content">
          <h1 className="tho-browse-hero-title">Find Your Perfect Home</h1>
          <p className="tho-browse-hero-subtitle">
            Browse {homes.length} manufactured homes — {newCount} new, {preOwnedCount} pre-owned — with real photos and {tourCount} 3D virtual tours
          </p>

          {/* Search Bar */}
          <div className="tho-browse-search-bar">
            <Search size={20} className="text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by name, manufacturer, beds, baths, type..."
              className="tho-browse-search-input"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="text-gray-400 hover:text-gray-600">
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

      {/* Toolbar */}
      <div className="tho-browse-toolbar" style={{display:'flex', flexDirection:'column', alignItems:'center', gap:'0.75rem', padding:'1rem 1rem 0.5rem'}}>
        <div className="tho-browse-toolbar-left" style={{display:'flex', alignItems:'center', gap:'0.75rem', justifyContent:'center', flexWrap:'wrap'}}>
          <span className="tho-browse-count">
            {sortedHomes.length} home{sortedHomes.length !== 1 ? 's' : ''}
          </span>

          {/* Quick status toggles */}
          <div className="tho-browse-status-tabs">
            <button
              onClick={() => setFilters(f => ({ ...f, status: '' }))}
              className={`tho-status-tab ${filters.status === '' ? 'active' : ''}`}
            >All</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Available' }))}
              className={`tho-status-tab ${filters.status === 'Available' ? 'active' : ''}`}
            >New ({newCount})</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Pre-Owned' }))}
              className={`tho-status-tab ${filters.status === 'Pre-Owned' ? 'active' : ''}`}
            >Pre-Owned ({preOwnedCount})</button>
          </div>
        </div>

        <div className="tho-browse-toolbar-right" style={{display:'flex', alignItems:'center', gap:'0.5rem', justifyContent:'center'}}>
          {/* Sort dropdown */}
          <div className="tho-sort-wrap">
            <ArrowUpDown size={14} />
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="tho-sort-select"
            >
              {SORT_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`tho-filter-btn ${hasActiveFilters ? 'active' : ''}`}
          >
            <SlidersHorizontal size={16} />
            Filters
            {hasActiveFilters && (
              <span className="tho-filter-badge">
                {Object.values(filters).filter(v => v !== '').length + (searchQuery ? 1 : 0)}
              </span>
            )}
          </button>
          {hasActiveFilters && (
            <button onClick={clearFilters} className="tho-clear-btn">
              Clear All
            </button>
          )}
        </div>
      </div>

      {/* Expandable Filter Panel */}
      {showFilters && (
        <div className="tho-browse-filter-panel">
          <div className="tho-filter-grid">
            <div className="tho-filter-group">
              <label><Bed size={14} /> Bedrooms</label>
              <select value={filters.beds} onChange={e => setFilters(f => ({ ...f, beds: e.target.value }))}>
                <option value="">Any</option>
                <option value="1">1+</option>
                <option value="2">2+</option>
                <option value="3">3+</option>
                <option value="4">4+</option>
              </select>
            </div>
            <div className="tho-filter-group">
              <label><Bath size={14} /> Bathrooms</label>
              <select value={filters.baths} onChange={e => setFilters(f => ({ ...f, baths: e.target.value }))}>
                <option value="">Any</option>
                <option value="1">1+</option>
                <option value="2">2+</option>
                <option value="3">3+</option>
              </select>
            </div>
            <div className="tho-filter-group">
              <label><Home size={14} /> Type</label>
              <select value={filters.classification} onChange={e => setFilters(f => ({ ...f, classification: e.target.value }))}>
                <option value="">Any</option>
                <option value="Single Wide">Single Wide</option>
                <option value="Double Wide">Double Wide</option>
              </select>
            </div>
            <div className="tho-filter-group">
              <label>Min Price</label>
              <input
                type="number"
                placeholder="$0"
                value={filters.minPrice}
                onChange={e => setFilters(f => ({ ...f, minPrice: e.target.value }))}
              />
            </div>
            <div className="tho-filter-group">
              <label>Max Price</label>
              <input
                type="number"
                placeholder="No max"
                value={filters.maxPrice}
                onChange={e => setFilters(f => ({ ...f, maxPrice: e.target.value }))}
              />
            </div>
          </div>
        </div>
      )}

      {/* Home Cards Grid */}
      <div className="tho-browse-grid">
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
        <div className="tho-browse-empty">
          <div style={{ background: '#f1f5f9', borderRadius: '50%', padding: 20, marginBottom: 12 }}>
            <Search size={36} className="text-gray-400" />
          </div>
          <p className="text-gray-700 font-medium text-lg">No homes match your search</p>
          <p className="text-gray-500 text-sm mt-1 mb-4">Try adjusting your filters or search terms</p>
          <button
            onClick={clearFilters}
            className="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 active:scale-95 transition"
          >
            Clear All Filters
          </button>
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
  const photoCount = (home.real_photos || home.gallery_images || []).length;
  const heroImage = (home.real_photos?.[0]) || home.image_url || home.floor_plan_url || '';
  const hasTour = !!home.matterport_id;
  const specs = home.specs || {};
  const categories = home.image_categories || {};
  const isNew = home.status === 'Available';

  return (
    <div className="tho-home-card">
      {/* Image — click opens detail */}
      <div className="tho-card-image-wrap" onClick={onClick}>
        {heroImage ? (
          <img
            src={heroImage}
            alt={home.model_name}
            className="tho-card-image"
            loading="lazy"
            onError={(e) => { e.target.src = ''; e.target.classList.add('tho-img-error'); }}
          />
        ) : (
          <div className="tho-card-image-placeholder">
            <Home size={32} className="text-gray-300" />
          </div>
        )}

        {/* Badges overlay */}
        <div className="tho-card-badges">
          <span className={`tho-card-status ${isNew ? 'new' : 'preowned'}`}>
            {isNew ? 'New' : 'Pre-Owned'}
          </span>
        </div>

        <div className="tho-card-bottom-badges">
          {photoCount > 1 && (
            <span className="tho-card-photo-count">
              <Camera size={12} /> {photoCount}
            </span>
          )}
          {hasTour && (
            <span className="tho-card-tour-badge">
              <Box size={12} /> 3D Tour
            </span>
          )}
        </div>
      </div>

      {/* Info */}
      <div className="tho-card-info">
        <h3 className="tho-card-name" onClick={onClick}>{home.model_name}</h3>
        <p className="tho-card-manufacturer">{home.manufacturer || 'New Vision Manufacturing'}</p>

        <div className="tho-card-specs">
          {specs.beds && (
            <span><Bed size={14} /> {specs.beds} Bed</span>
          )}
          {specs.baths && (
            <span><Bath size={14} /> {specs.baths} Bath</span>
          )}
          {specs.sq_ft && (
            <span><Maximize2 size={14} /> {specs.sq_ft.toLocaleString()} sqft</span>
          )}
        </div>

        {/* Room category badges */}
        {Object.keys(categories).length > 0 && (
          <div className="tho-card-categories">
            {categories.kitchen && <span className="tho-cat-badge kitchen">Kitchen</span>}
            {categories.bedroom && <span className="tho-cat-badge bedroom">Bedroom</span>}
            {categories.bathroom && <span className="tho-cat-badge bathroom">Bathroom</span>}
          </div>
        )}

        <div className="tho-card-price">
          {home.display_price && home.display_price !== 'Call for Price'
            ? home.display_price
            : <span className="tho-call-price">Call for Price</span>
          }
        </div>

        {/* Dual action buttons */}
        <div className="tho-card-actions">
          <button className="tho-card-view-btn" onClick={onClick}>
            <Eye size={16} /> View Details
          </button>
          <button
            className="tho-card-tour-btn"
            onClick={(e) => { e.stopPropagation(); onScheduleTour(); }}
          >
            <Calendar size={16} /> Schedule Tour
          </button>
        </div>
      </div>
    </div>
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
  const floorPlan = home.floor_plan_url;
  const isCallForPrice = !home.display_price || home.display_price === 'Call for Price';

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
    <div className="tho-detail-overlay" onClick={onClose}>
      <div className="tho-detail-modal" onClick={e => e.stopPropagation()}>
        {/* Sticky header — name, price, close */}
        <div className="tho-detail-sticky-header">
          <div className="tho-detail-sticky-title">
            <h2 className="tho-detail-sticky-name">{home.model_name}</h2>
            <span className="tho-detail-sticky-price">
              {!isCallForPrice ? home.display_price : 'Call for Price'}
            </span>
          </div>
          <button onClick={onClose} className="tho-detail-sticky-close" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        {/* Photo Gallery Section */}
        <div className="tho-detail-gallery">
          {showTour && hasTour ? (
            <div className="tho-detail-tour-wrap">
              <iframe
                src={`${MATTERPORT_BASE}${home.matterport_id}&play=1`}
                title={`${home.model_name} 3D Tour`}
                className="tho-detail-tour-iframe"
                allowFullScreen
              />
            </div>
          ) : photos.length > 0 ? (
            <div
              className="tho-detail-photo-main"
              onTouchStart={handleTouchStart}
              onTouchEnd={handleTouchEnd}
            >
              <img
                src={photos[activePhotoIndex]}
                alt={`${home.model_name} photo ${activePhotoIndex + 1}`}
                className="tho-detail-main-img"
                onError={(e) => { e.target.classList.add('tho-img-error'); }}
              />
              {photos.length > 1 && (
                <>
                  <button onClick={onPrevPhoto} className="tho-gallery-nav left" aria-label="Previous photo">
                    <ChevronLeft size={24} />
                  </button>
                  <button onClick={onNextPhoto} className="tho-gallery-nav right" aria-label="Next photo">
                    <ChevronRight size={24} />
                  </button>
                  <span className="tho-photo-counter">
                    {activePhotoIndex + 1} / {photos.length}
                  </span>
                </>
              )}
            </div>
          ) : (
            <div className="tho-detail-no-photo">
              <Home size={48} className="text-gray-300" />
              <p>No photos available</p>
            </div>
          )}

          {/* Category Tabs + Thumbnail Strip */}
          <div className="tho-detail-gallery-controls">
            <div className="tho-detail-view-toggle">
              <button
                className={`tho-view-tab ${!showTour ? 'active' : ''}`}
                onClick={() => { if (showTour) onToggleTour(); }}
              >
                <Camera size={14} /> Photos ({(home.real_photos || home.gallery_images || []).length})
              </button>
              {hasTour && (
                <button
                  className={`tho-view-tab ${showTour ? 'active' : ''}`}
                  onClick={() => { if (!showTour) onToggleTour(); }}
                >
                  <Box size={14} /> 3D Tour
                </button>
              )}
              {floorPlan && (
                <a href={floorPlan} target="_blank" rel="noopener noreferrer" className="tho-view-tab">
                  <Grid3X3 size={14} /> Floor Plan
                </a>
              )}
            </div>

            {categoryKeys.length > 0 && !showTour && (
              <div className="tho-detail-cat-tabs">
                <button
                  className={`tho-dcat-tab ${activeCategory === 'all' ? 'active' : ''}`}
                  onClick={() => onSetCategory('all')}
                >All</button>
                {categoryKeys.map(cat => (
                  <button
                    key={cat}
                    className={`tho-dcat-tab ${activeCategory === cat ? 'active' : ''}`}
                    onClick={() => onSetCategory(cat)}
                  >
                    {cat.charAt(0).toUpperCase() + cat.slice(1)} ({categories[cat].length})
                  </button>
                ))}
              </div>
            )}

            {!showTour && photos.length > 1 && (
              <div className="tho-detail-thumbs">
                {photos.map((photo, idx) => (
                  <button
                    key={idx}
                    className={`tho-thumb ${idx === activePhotoIndex ? 'active' : ''}`}
                    onClick={() => onSetPhotoIndex(idx)}
                  >
                    <img src={photo} alt={`Thumb ${idx + 1}`} loading="lazy" />
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Info Section */}
        <div className="tho-detail-info">
          <div className="tho-detail-header">
            <div>
              <h2 className="tho-detail-name">{home.model_name}</h2>
              <p className="tho-detail-manufacturer">{home.manufacturer || 'New Vision Manufacturing'}</p>
            </div>
            <span className={`tho-detail-status ${home.status === 'Available' ? 'new' : 'preowned'}`}>
              {home.status === 'Available' ? 'New' : 'Pre-Owned'}
            </span>
          </div>

          {/* Specs Row */}
          <div className="tho-detail-specs-row">
            {specs.beds && (
              <div className="tho-detail-spec">
                <Bed size={20} />
                <span className="tho-spec-value">{specs.beds}</span>
                <span className="tho-spec-label">Beds</span>
              </div>
            )}
            {specs.baths && (
              <div className="tho-detail-spec">
                <Bath size={20} />
                <span className="tho-spec-value">{specs.baths}</span>
                <span className="tho-spec-label">Baths</span>
              </div>
            )}
            {specs.sq_ft && (
              <div className="tho-detail-spec">
                <Maximize2 size={20} />
                <span className="tho-spec-value">{specs.sq_ft.toLocaleString()}</span>
                <span className="tho-spec-label">Sq Ft</span>
              </div>
            )}
            {specs.dimensions && (
              <div className="tho-detail-spec">
                <Grid3X3 size={20} />
                <span className="tho-spec-value">{specs.dimensions}</span>
                <span className="tho-spec-label">Dimensions</span>
              </div>
            )}
          </div>

          {/* Price */}
          <div className="tho-detail-price-section">
            <span className="tho-detail-price">
              {!isCallForPrice ? home.display_price : 'Call for Price'}
            </span>
            {isCallForPrice && (
              <button className="tho-get-price-btn" onClick={onGetPrice}>
                <DollarSign size={16} /> Get Price Quote
              </button>
            )}
          </div>

          {/* Features */}
          {home.features && home.features.length > 0 && (
            <div className="tho-detail-features">
              <h4>Features</h4>
              <ul>
                {home.features.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}

          {/* Action Buttons */}
          <div className="tho-detail-actions">
            <button
              className="tho-detail-ask-btn"
              onClick={() => {
                onClose();
                onAskTex(`Tell me more about the ${home.model_name}. What are its key features, pricing, and availability?`);
              }}
            >
              <MessageCircle size={18} /> Ask Tex About This Home
            </button>
            <button className="tho-detail-schedule-btn" onClick={onScheduleTour}>
              <Calendar size={18} /> Schedule a Tour
            </button>
          </div>

          <div className="tho-detail-secondary-actions">
            <a href={`tel:${BUSINESS_PHONE_RAW}`} className="tho-detail-call-btn">
              <Phone size={16} /> Call {BUSINESS_PHONE}
            </a>
            {onCreateAd && (
              <button
                className="tho-detail-ad-btn"
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
          <div className="tho-detail-location">
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
    <div className="tho-similar-homes">
      <h4 className="tho-similar-title">
        <Box size={16} /> Similar Homes
      </h4>
      <div className="tho-similar-grid">
        {scored.map(({ home }) => (
          <button
            key={home.id}
            className="tho-similar-card"
            onClick={() => onSelectHome(home)}
          >
            <div className="tho-similar-img-wrap">
              {home.image_url ? (
                <img src={home.image_url} alt={home.model_name} loading="lazy" />
              ) : (
                <Home size={24} className="text-gray-300" />
              )}
            </div>
            <div className="tho-similar-info">
              <div className="tho-similar-name">{home.model_name}</div>
              <div className="tho-similar-specs">
                {home.specs?.beds}BR · {home.specs?.baths}BA · {home.specs?.sq_ft?.toLocaleString()} sqft
              </div>
              <div className="tho-similar-price">
                {home.display_price || 'Call for Price'}
              </div>
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
    <div className="tho-lead-overlay" onClick={onClose}>
      <div className="tho-lead-modal" onClick={e => e.stopPropagation()}>
        <button onClick={onClose} className="tho-lead-close" aria-label="Close">
          <X size={20} />
        </button>

        {submitted ? (
          <div className="tho-lead-success">
            <CheckCircle2 size={48} className="text-green-500" />
            <h3>Thank You!</h3>
            <p>We received your {type === 'tour' ? 'tour request' : 'price quote request'} for the <strong>{home.model_name}</strong>. Our team will contact you shortly.</p>
            <button onClick={onClose} className="tho-lead-done-btn">Done</button>
          </div>
        ) : (
          <>
            <div className="tho-lead-header">
              <h3>{type === 'tour' ? 'Schedule a Tour' : 'Get a Price Quote'}</h3>
              <p className="tho-lead-home-name">
                {type === 'tour' ? 'Visit' : 'Get pricing for'} the <strong>{home.model_name}</strong>
                {home.specs?.beds && ` — ${home.specs.beds} Bed, ${home.specs.baths} Bath`}
                {home.specs?.sq_ft && `, ${home.specs.sq_ft.toLocaleString()} sqft`}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="tho-lead-form">
              <div className="tho-lead-field">
                <label>Name *</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={e => setFormData(f => ({ ...f, name: e.target.value }))}
                  placeholder="Your full name"
                  required
                />
              </div>
              <div className="tho-lead-field">
                <label>Phone *</label>
                <input
                  type="tel"
                  value={formData.phone}
                  onChange={e => setFormData(f => ({ ...f, phone: e.target.value }))}
                  placeholder="(281) 000-0000"
                  required
                />
              </div>
              <div className="tho-lead-field">
                <label>Email</label>
                <input
                  type="email"
                  value={formData.email}
                  onChange={e => setFormData(f => ({ ...f, email: e.target.value }))}
                  placeholder="you@email.com"
                />
              </div>
              <div className="tho-lead-field">
                <label>{type === 'tour' ? 'Preferred date/time' : 'Additional details'}</label>
                <textarea
                  value={formData.message}
                  onChange={e => setFormData(f => ({ ...f, message: e.target.value }))}
                  placeholder={type === 'tour' ? 'e.g., Saturday morning, weekday after 5pm...' : 'Any questions or preferences...'}
                  rows={3}
                />
              </div>

              {error && (
                <div className="tho-lead-error">
                  <AlertCircle size={14} /> {error}
                </div>
              )}

              <button type="submit" className="tho-lead-submit-btn" disabled={submitting}>
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
