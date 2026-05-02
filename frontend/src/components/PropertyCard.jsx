import React, { useState } from 'react';
import { Home, Bed, Bath, Maximize, Plus, Check, ChevronLeft, ChevronRight, X, Images } from 'lucide-react';

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
                    alt={`${modelName} - Photo ${currentImageIndex + 1} of ${allImages.length}`}
                    className="max-w-full max-h-[85vh] object-contain rounded-lg"
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
                                <img src={img} alt="" className="w-full h-full object-cover" />
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

const PropertyCard = ({ property, onToggleCompare, isSelected }) => {
    const {
        model_name,
        specs,
        pricing,
        classification,
        manufacturer,
        image_url,
        gallery_images = []
    } = property;

    const [imageError, setImageError] = useState(false);
    const [currentImageIndex, setCurrentImageIndex] = useState(0);
    const [showGallery, setShowGallery] = useState(false);

    // Combine primary image with gallery images
    const allImages = image_url
        ? [image_url, ...(gallery_images?.filter(img => img !== image_url) || [])]
        : gallery_images || [];

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
                                alt={model_name}
                                loading="lazy"
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
                    ) : (
                        <Home size={48} className="text-gray-300" />
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

                    {/* Specs Grid */}
                    <div className="grid grid-cols-3 gap-1 py-3 border-t border-b border-gray-100">
                        <div className="flex flex-col items-center">
                            <div className="flex items-center text-gray-700 mb-0.5">
                                <Bed size={14} className="mr-1 text-gray-400" />
                                <span className="font-semibold text-sm">{specs?.beds || '-'}</span>
                            </div>
                            <span className="text-xs text-gray-400">Beds</span>
                        </div>
                        <div className="flex flex-col items-center border-l border-gray-100">
                            <div className="flex items-center text-gray-700 mb-0.5">
                                <Bath size={14} className="mr-1 text-gray-400" />
                                <span className="font-semibold text-sm">{specs?.baths || '-'}</span>
                            </div>
                            <span className="text-xs text-gray-400">Baths</span>
                        </div>
                        <div className="flex flex-col items-center border-l border-gray-100">
                            <div className="flex items-center text-gray-700 mb-0.5">
                                <Maximize size={14} className="mr-1 text-gray-400" />
                                <span className="font-semibold text-sm">{specs?.sq_ft?.toLocaleString() || '-'}</span>
                            </div>
                            <span className="text-xs text-gray-400">Sq Ft</span>
                        </div>
                    </div>

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
