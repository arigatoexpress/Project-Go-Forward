import React, { useState } from 'react';
import { Home, Bed, Bath, Maximize, Plus, Check, ChevronLeft, ChevronRight, X, Images } from 'lucide-react';
import { generateSrcSet, getImageSizes } from '../utils/imageOptimization';

function GalleryModal({
    allImages,
    currentImageIndex,
    hasMultipleImages,
    modelName,
    onClose,
    onNext,
    onPrev,
    onSelectImage,
}) {
    return (
        <div
            className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center"
            onClick={onClose}
            aria-hidden="true"
        >
            <button
                onClick={(e) => { e.stopPropagation(); onClose(); }}
                className="absolute top-4 right-4 text-white hover:text-gray-300 z-50"
                aria-label="Close photo gallery"
            >
                <X size={32} aria-hidden="true" />
            </button>

            <div
                className="relative w-full h-full flex items-center justify-center p-4"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-label={`${modelName} photo gallery`}
            >
                {hasMultipleImages && (
                    <button
                        onClick={onPrev}
                        className="absolute left-4 z-10 p-2 bg-black/50 hover:bg-black/70 rounded-full text-white"
                        aria-label="Previous photo"
                    >
                        <ChevronLeft size={32} aria-hidden="true" />
                    </button>
                )}

                <img
                    src={allImages[currentImageIndex]}
                    srcSet={generateSrcSet(allImages[currentImageIndex])}
                    sizes={getImageSizes('modal')}
                    alt={`${modelName} - Photo ${currentImageIndex + 1} of ${allImages.length}`}
                    className="max-w-full max-h-[85vh] object-contain rounded-lg"
                    loading="lazy"
                    decoding="async"
                />

                {hasMultipleImages && (
                    <button
                        onClick={onNext}
                        className="absolute right-4 z-10 p-2 bg-black/50 hover:bg-black/70 rounded-full text-white"
                        aria-label="Next photo"
                    >
                        <ChevronRight size={32} aria-hidden="true" />
                    </button>
                )}

                {hasMultipleImages && (
                    <div className="absolute bottom-4 left-1/2 transform -translate-x-1/2 flex gap-2 p-2 bg-black/50 rounded-lg max-w-[90vw] overflow-x-auto">
                        {allImages.map((img, idx) => (
                            <button
                                key={idx}
                                onClick={(e) => { e.stopPropagation(); onSelectImage(idx); }}
                                className={`flex-shrink-0 w-16 h-12 rounded overflow-hidden border-2 transition-all ${idx === currentImageIndex ? 'border-blue-500' : 'border-transparent hover:border-white/50'
                                    }`}
                                aria-label={`View photo ${idx + 1} of ${allImages.length}`}
                                aria-current={idx === currentImageIndex ? 'true' : undefined}
                            >
                                <img
                                    src={img}
                                    srcSet={generateSrcSet(img)}
                                    sizes={getImageSizes('modal-thumbnail')}
                                    alt=""
                                    className="w-full h-full object-cover"
                                    loading="lazy"
                                    decoding="async"
                                />
                            </button>
                        ))}
                    </div>
                )}

                <div className="absolute top-4 left-4 text-white text-sm bg-black/50 px-3 py-1 rounded-full">
                    {currentImageIndex + 1} / {allImages.length}
                </div>
            </div>
        </div>
    );
}

const FLOORPLAN_TOKENS = ['floorplan', 'floor-plan', 'floor_plan', 'floor-plans', 'floor_plans', 'floor plans'];
const PHOTO_FILENAME_TOKENS = ['bath', 'bed', 'coffee', 'exterior', 'front', 'interior', 'island', 'kitchen', 'living', 'porch', 'room', 'utility'];
const SHORT_PHOTO_FILENAME_TOKENS = ['3d', 'ext', 'int', 'kit'];
const UUID_FILENAME_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

const PropertyCard = ({ property, onToggleCompare, isSelected }) => {
    const {
        model_name,
        specs,
        pricing,
        classification,
        manufacturer,
        image_url,
        gallery_images = [],
        real_photos = [],
        floorplan_url,
        floor_plan_url,
        floorplan_urls = [],
        media_quality = {},
        is_orderable = false,
    } = property;

    const [imageError, setImageError] = useState(false);
    const [currentImageIndex, setCurrentImageIndex] = useState(0);
    const [showGallery, setShowGallery] = useState(false);

    // Hero-first ordering for the card image.
    //
    // After PR #43 (`feat/inventory-photo-classification`) the backend
    // guarantees `image_url` is a non-floorplan exterior URL (or empty).
    // We pick the primary image deterministically from
    // `image_url -> real_photos[0] -> gallery_images[0]` so the card
    // never surfaces a floorplan as its hero image.
    const floorplanUrls = [
        floorplan_url,
        floor_plan_url,
        ...(Array.isArray(floorplan_urls) ? floorplan_urls : []),
    ].filter(Boolean);
    const imageCandidates = [
        image_url,
        ...(Array.isArray(real_photos) ? real_photos : []),
        ...(Array.isArray(gallery_images) ? gallery_images : []),
    ];
    const allImages = imageCandidates.filter((img, index, values) => (
        img && values.indexOf(img) === index && !isFloorplanImage(img, floorplanUrls)
    ));
    const hasFloorplanOnly = !allImages.length && (
        media_quality?.status === 'floorplan_only' || floorplanUrls.length > 0
    );

    // Order-only / build-to-order homes never carry stock photography — these
    // models are built to order, so the absence of a photo is by design, not a
    // broken listing. When such a home has no real photo we render a BRANDED
    // placeholder + badge so it reads as intentional (optics fix), instead of a
    // bare "no image" state or a broken <img>.
    const isOrderOnlyNoPhotos = !allImages.length && is_orderable === true;

    const hasMultipleImages = allImages.length > 1;

    const nextImage = (e) => {
        e?.stopPropagation();
        setCurrentImageIndex((prev) => (prev + 1) % allImages.length);
    };

    const prevImage = (e) => {
        e?.stopPropagation();
        setCurrentImageIndex((prev) => (prev - 1 + allImages.length) % allImages.length);
    };

    return (
        <>
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden hover:shadow-lg transition-all duration-200 my-4 w-full max-w-2xl">
                {/* Home Image with Gallery */}
                <div
                    className="h-52 bg-gray-100 flex items-center justify-center relative overflow-hidden cursor-pointer group"

                    onClick={() => allImages.length > 0 && !imageError && setShowGallery(true)}
                >
                    {allImages.length > 0 && !imageError ? (
                        <>
                            <img
                                src={allImages[currentImageIndex]}
                                srcSet={generateSrcSet(allImages[currentImageIndex])}
                                sizes={getImageSizes('card-hero')}
                                alt={model_name}
                                loading="lazy"
                                decoding="async"
                                className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                                onError={() => setImageError(true)}
                            />

                            {/* Hover overlay */}
                            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all duration-200 flex items-center justify-center">
                                <div className="opacity-0 group-hover:opacity-100 transition-opacity bg-white/90 px-3 py-2 rounded-lg text-sm font-medium text-gray-800 flex items-center gap-2">
                                    <Images size={16} />
                                    View Photos {allImages.length > 1 ? `(${allImages.length})` : ''}
                                </div>
                            </div>

                            {/* Image navigation arrows */}
                            {hasMultipleImages && (
                                <>
                                    <button
                                        onClick={prevImage}
                                        className="absolute left-2 top-1/2 -translate-y-1/2 p-1.5 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity"
                                        aria-label="Previous photo"
                                    >
                                        <ChevronLeft size={20} aria-hidden="true" />
                                    </button>
                                    <button
                                        onClick={nextImage}
                                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity"
                                        aria-label="Next photo"
                                    >
                                        <ChevronRight size={20} aria-hidden="true" />
                                    </button>
                                </>
                            )}

                            {/* Dot indicators */}
                            {hasMultipleImages && (
                                <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex gap-1.5">
                                    {allImages.slice(0, 5).map((_, idx) => (
                                        <button
                                            key={idx}
                                            onClick={(e) => { e.stopPropagation(); setCurrentImageIndex(idx); }}
                                            className={`w-2 h-2 rounded-full transition-all ${idx === currentImageIndex ? 'bg-white w-4' : 'bg-white/60 hover:bg-white/80'
                                                }`}
                                            aria-label={`Photo ${idx + 1} of ${allImages.length}`}
                                            aria-current={idx === currentImageIndex ? 'true' : undefined}
                                        />
                                    ))}
                                    {allImages.length > 5 && (
                                        <span className="text-white text-xs ml-1">+{allImages.length - 5}</span>
                                    )}
                                </div>
                            )}
                        </>
                    ) : isOrderOnlyNoPhotos ? (
                        /* Branded build-to-order placeholder. role="img" + alt-style
                           label keeps it accessible without rendering a broken <img>. */
                        <div
                            role="img"
                            aria-label={`${model_name} — Build-to-Order model. Photos available on request; this home is built to order.`}
                            className="tho-order-only-placeholder w-full h-full flex flex-col items-center justify-center gap-2 bg-gradient-to-br from-blue-50 to-gray-100 text-center px-4"
                        >
                            <Home size={44} className="text-blue-300" aria-hidden="true" />
                            <span className="text-sm font-semibold text-blue-700">Build-to-Order</span>
                            <span className="text-xs text-gray-500 max-w-[14rem]">
                                Photos available on request — this model is built to order
                            </span>
                        </div>
                    ) : (
                        <div className="flex flex-col items-center gap-2 text-gray-400">
                            <Home size={48} className="text-gray-300" aria-hidden="true" />
                            {hasFloorplanOnly && (
                                <span className="text-xs font-medium">Floorplan available</span>
                            )}
                        </div>
                    )}

                    {/* Order-only badge — top-left so it never collides with the
                        classification badge on the right. */}
                    {isOrderOnlyNoPhotos && (
                        <div className="tho-order-only-badge absolute top-2 left-2 bg-blue-600 text-white px-2.5 py-1 rounded-full text-xs font-semibold shadow">
                            <span aria-hidden="true">Order-Only Model</span>
                            <span className="sr-only">Order-only model — built to order</span>
                        </div>
                    )}

                    {/* Classification badge */}
                    <div className="absolute top-2 right-2 bg-white/95 backdrop-blur-sm px-2.5 py-1 rounded-full text-xs font-semibold text-gray-700 shadow">
                        {classification}
                    </div>
                </div>

                <div className="p-4">
                    {/* Title and Price */}
                    <div className="flex justify-between items-start gap-2 mb-3">
                        <div className="flex-1 min-w-0">
                            <h3 className="font-bold text-gray-900 text-base leading-tight truncate" title={model_name}>
                                {model_name}
                            </h3>
                            <p className="text-sm text-gray-500 truncate">{manufacturer}</p>
                        </div>
                        <div className="text-right flex-shrink-0">
                            <div className="font-bold text-blue-600 text-base whitespace-nowrap">
                                {pricing?.display_price || 'Call for Price'}
                            </div>
                        </div>
                    </div>

                    {/* Specs Grid — hide individual spec when missing rather
                        than rendering a "-" placeholder. Column count adjusts
                        so the grid stays balanced. */}
                    {(() => {
                        const specCells = [];
                        if (specs?.beds) {
                            specCells.push(
                                <div key="beds" className="flex flex-col items-center">
                                    <div className="flex items-center text-gray-700 mb-0.5">
                                        <Bed size={14} className="mr-1 text-gray-400" />
                                        <span className="font-semibold text-sm">{specs.beds}</span>
                                    </div>
                                    <span className="text-xs text-gray-500">Beds</span>
                                </div>
                            );
                        }
                        if (specs?.baths) {
                            specCells.push(
                                <div key="baths" className="flex flex-col items-center">
                                    <div className="flex items-center text-gray-700 mb-0.5">
                                        <Bath size={14} className="mr-1 text-gray-400" />
                                        <span className="font-semibold text-sm">{specs.baths}</span>
                                    </div>
                                    <span className="text-xs text-gray-500">Baths</span>
                                </div>
                            );
                        }
                        if (specs?.sq_ft) {
                            specCells.push(
                                <div key="sqft" className="flex flex-col items-center">
                                    <div className="flex items-center text-gray-700 mb-0.5">
                                        <Maximize size={14} className="mr-1 text-gray-400" />
                                        <span className="font-semibold text-sm">{specs.sq_ft.toLocaleString()}</span>
                                    </div>
                                    <span className="text-xs text-gray-500">Sq Ft</span>
                                </div>
                            );
                        }
                        if (specCells.length === 0) return null;
                        const colCls = specCells.length === 3 ? 'grid-cols-3'
                            : specCells.length === 2 ? 'grid-cols-2'
                            : 'grid-cols-1';
                        return (
                            <div className={`grid ${colCls} gap-1 py-3 border-t border-b border-gray-100`}>
                                {specCells}
                            </div>
                        );
                    })()}

                    {/* Action Buttons */}
                    <div className="mt-3 flex gap-2">
                        <button
                            onClick={() => onToggleCompare(property)}
                            className={`flex-1 flex items-center justify-center py-2 px-2 rounded-lg text-sm font-medium transition-colors ${isSelected
                                ? 'bg-green-50 text-green-700 border border-green-200'
                                : 'bg-gray-50 text-gray-700 border border-gray-200 hover:bg-gray-100'
                                }`}
                        >
                            {isSelected ? (
                                <>
                                    <Check size={14} className="mr-1" />
                                    Added
                                </>
                            ) : (
                                <>
                                    <Plus size={14} className="mr-1" />
                                    Compare
                                </>
                            )}
                        </button>

                        <button
                            onClick={() => allImages.length > 0 && setShowGallery(true)}
                            className="flex-1 bg-blue-600 text-white py-2 px-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
                        >
                            View Details
                        </button>
                    </div>
                </div>
            </div>

            {/* Gallery Modal */}
            {showGallery && (
                <GalleryModal
                    allImages={allImages}
                    currentImageIndex={currentImageIndex}
                    hasMultipleImages={hasMultipleImages}
                    modelName={model_name}
                    onClose={() => setShowGallery(false)}
                    onNext={nextImage}
                    onPrev={prevImage}
                    onSelectImage={setCurrentImageIndex}
                />
            )}
        </>
    );
};

export default PropertyCard;
