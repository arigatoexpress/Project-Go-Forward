import React, { useState, useEffect, useCallback } from 'react';
import {
  Search, SlidersHorizontal, X, Home, Bed, Bath, Maximize2,
  Camera, Box, ChevronLeft, ChevronRight, MapPin, Phone,
  MessageCircle, Grid3X3, LayoutList, Loader2, Eye
} from 'lucide-react';
import './InventoryBrowse.css';

const CDN_BASE = "https://d132mt2yijm03y.cloudfront.net";
const MATTERPORT_BASE = "https://my.matterport.com/show/?m=";

export default function InventoryBrowse({ onAskTex, onBack }) {
  const [homes, setHomes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedHome, setSelectedHome] = useState(null);
  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
  const [activeCategory, setActiveCategory] = useState('all');
  const [showTour, setShowTour] = useState(false);

  // Filters
  const [filters, setFilters] = useState({
    status: '', // '' | 'Available' | 'Pre-Owned'
    beds: '',
    baths: '',
    minPrice: '',
    maxPrice: '',
  });
  const [showFilters, setShowFilters] = useState(false);

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

  // Filter and search logic
  const filteredHomes = homes.filter(home => {
    // Search query
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const name = (home.model_name || '').toLowerCase();
      const mfr = (home.manufacturer || '').toLowerCase();
      if (!name.includes(q) && !mfr.includes(q)) return false;
    }
    // Status filter
    if (filters.status && home.status !== filters.status) return false;
    // Beds filter
    if (filters.beds) {
      const beds = home.specs?.beds || 0;
      if (beds < parseInt(filters.beds)) return false;
    }
    // Baths filter
    if (filters.baths) {
      const baths = home.specs?.baths || 0;
      if (baths < parseInt(filters.baths)) return false;
    }
    // Price range
    if (filters.minPrice) {
      const price = home.price_value || 0;
      if (price > 0 && price < parseFloat(filters.minPrice)) return false;
    }
    if (filters.maxPrice) {
      const price = home.price_value || 0;
      if (price > 0 && price > parseFloat(filters.maxPrice)) return false;
    }
    return true;
  });

  const clearFilters = () => {
    setFilters({ status: '', beds: '', baths: '', minPrice: '', maxPrice: '' });
    setSearchQuery('');
  };

  const hasActiveFilters = Object.values(filters).some(v => v !== '') || searchQuery;

  // Photo helpers for detail modal
  const getPhotosForCategory = useCallback((home) => {
    if (!home) return [];
    const allPhotos = home.real_photos || home.gallery_images || [];
    if (activeCategory === 'all' || !home.image_categories) return allPhotos;

    const catFiles = home.image_categories[activeCategory] || [];
    if (catFiles.length === 0) return allPhotos;

    // Build full URLs from filenames
    const planId = home.id ? null : null; // Website homes have full URLs already
    return catFiles.map(f => {
      // If already a full URL, return as-is
      if (f.startsWith('http')) return f;
      // Try to find matching full URL from allPhotos
      const match = allPhotos.find(url => url.includes(f));
      return match || f;
    });
  }, [activeCategory]);

  const openDetail = (home) => {
    setSelectedHome(home);
    setActivePhotoIndex(0);
    setActiveCategory('all');
    setShowTour(false);
  };

  const closeDetail = () => {
    setSelectedHome(null);
    setActivePhotoIndex(0);
    setActiveCategory('all');
    setShowTour(false);
  };

  const photos = getPhotosForCategory(selectedHome);

  const nextPhoto = () => {
    if (photos.length > 0) {
      setActivePhotoIndex((prev) => (prev + 1) % photos.length);
    }
  };

  const prevPhoto = () => {
    if (photos.length > 0) {
      setActivePhotoIndex((prev) => (prev - 1 + photos.length) % photos.length);
    }
  };

  // Keyboard navigation for photo gallery
  useEffect(() => {
    if (!selectedHome) return;
    const handler = (e) => {
      if (e.key === 'ArrowRight') nextPhoto();
      else if (e.key === 'ArrowLeft') prevPhoto();
      else if (e.key === 'Escape') closeDetail();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectedHome, photos.length]);

  // Reset photo index when category changes
  useEffect(() => {
    setActivePhotoIndex(0);
  }, [activeCategory]);

  // --- RENDER ---

  if (loading) {
    return (
      <div className="tho-browse-loading">
        <Loader2 className="h-10 w-10 animate-spin text-blue-600" />
        <p className="mt-4 text-gray-500">Loading inventory...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="tho-browse-loading">
        <p className="text-red-500">Error: {error}</p>
        <button onClick={fetchInventory} className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="tho-browse">
      {/* Hero Section */}
      <div className="tho-browse-hero">
        <div className="tho-browse-hero-content">
          <h1 className="tho-browse-hero-title">Find Your Perfect Home</h1>
          <p className="tho-browse-hero-subtitle">
            Browse {homes.length} manufactured homes — new and pre-owned — with real photos, floor plans, and 3D tours
          </p>

          {/* Search Bar */}
          <div className="tho-browse-search-bar">
            <Search size={20} className="text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by name, manufacturer..."
              className="tho-browse-search-input"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="text-gray-400 hover:text-gray-600">
                <X size={18} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="tho-browse-toolbar">
        <div className="tho-browse-toolbar-left">
          <span className="tho-browse-count">
            {filteredHomes.length} home{filteredHomes.length !== 1 ? 's' : ''}
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
            >New</button>
            <button
              onClick={() => setFilters(f => ({ ...f, status: 'Pre-Owned' }))}
              className={`tho-status-tab ${filters.status === 'Pre-Owned' ? 'active' : ''}`}
            >Pre-Owned</button>
          </div>
        </div>

        <div className="tho-browse-toolbar-right">
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
        {filteredHomes.map((home, idx) => (
          <HomeCard key={home.id || idx} home={home} onClick={() => openDetail(home)} />
        ))}
      </div>

      {filteredHomes.length === 0 && !loading && (
        <div className="tho-browse-empty">
          <Home size={48} className="text-gray-300" />
          <p className="text-gray-500 mt-4">No homes match your search.</p>
          <button onClick={clearFilters} className="mt-2 text-blue-600 hover:underline">
            Clear filters
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
          onSetPhotoIndex={setActivePhotoIndex}
          onSetCategory={(cat) => setActiveCategory(cat)}
          onToggleTour={() => setShowTour(!showTour)}
          onAskTex={onAskTex}
        />
      )}
    </div>
  );
}


// ─── Home Card Component ───
function HomeCard({ home, onClick }) {
  const photoCount = (home.real_photos || home.gallery_images || []).length;
  const heroImage = (home.real_photos?.[0]) || home.image_url || home.floor_plan_url || '';
  const hasTour = !!home.matterport_id;
  const specs = home.specs || {};
  const categories = home.image_categories || {};
  const isNew = home.status === 'Available';

  return (
    <div className="tho-home-card" onClick={onClick}>
      {/* Image */}
      <div className="tho-card-image-wrap">
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
        <h3 className="tho-card-name">{home.model_name}</h3>
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

        <button className="tho-card-view-btn">
          <Eye size={16} /> View Details
        </button>
      </div>
    </div>
  );
}


// ─── Home Detail Modal ───
function HomeDetailModal({
  home, photos, activePhotoIndex, activeCategory, showTour,
  onClose, onPrevPhoto, onNextPhoto, onSetPhotoIndex, onSetCategory,
  onToggleTour, onAskTex
}) {
  const specs = home.specs || {};
  const categories = home.image_categories || {};
  const categoryKeys = Object.keys(categories);
  const hasTour = !!home.matterport_id;
  const floorPlan = home.floor_plan_url;

  return (
    <div className="tho-detail-overlay" onClick={onClose}>
      <div className="tho-detail-modal" onClick={e => e.stopPropagation()}>
        {/* Close button */}
        <button onClick={onClose} className="tho-detail-close" aria-label="Close">
          <X size={24} />
        </button>

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
            <div className="tho-detail-photo-main">
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
            {/* View toggle: Photos vs 3D Tour */}
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

            {/* Category filter tabs */}
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

            {/* Thumbnail strip */}
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
              {home.display_price && home.display_price !== 'Call for Price'
                ? home.display_price
                : 'Call for Price'
              }
            </span>
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
            <a href="tel:+12813243020" className="tho-detail-call-btn">
              <Phone size={18} /> Call (281) 324-3020
            </a>
          </div>

          {/* Location */}
          <div className="tho-detail-location">
            <MapPin size={14} />
            <span>10685 FM 1960 East, Huffman, TX — Mon-Fri 9-6, Sat 9-5</span>
          </div>
        </div>
      </div>
    </div>
  );
}
