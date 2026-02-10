import React, { useState } from 'react';
import { Search, SlidersHorizontal, X, Home, DollarSign, Bed, Bath } from 'lucide-react';

export default function SearchFilters({ onApplyFilters, onClear }) {
    const [isOpen, setIsOpen] = useState(false);
    const [filters, setFilters] = useState({
        bedrooms: '',
        bathrooms: '',
        minPrice: '',
        maxPrice: '',
        homeType: ''
    });

    const handleApply = () => {
        const activeFilters = {};

        if (filters.bedrooms) activeFilters.bedrooms = parseInt(filters.bedrooms);
        if (filters.bathrooms) activeFilters.bathrooms = parseInt(filters.bathrooms);
        if (filters.minPrice) activeFilters.minPrice = parseFloat(filters.minPrice);
        if (filters.maxPrice) activeFilters.maxPrice = parseFloat(filters.maxPrice);
        if (filters.homeType) activeFilters.homeType = filters.homeType;

        onApplyFilters(activeFilters);
        setIsOpen(false);
    };

    const handleClear = () => {
        setFilters({
            bedrooms: '',
            bathrooms: '',
            minPrice: '',
            maxPrice: '',
            homeType: ''
        });
        onClear();
    };

    const hasActiveFilters = Object.values(filters).some(v => v !== '');

    return (
        <div className="relative">
            {/* Filter Toggle Button */}
            <button
                onClick={() => setIsOpen(!isOpen)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-all ${hasActiveFilters
                        ? 'bg-blue-50 border-blue-300 text-blue-700'
                        : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
                    }`}
            >
                <SlidersHorizontal size={18} />
                <span className="font-medium">Filters</span>
                {hasActiveFilters && (
                    <span className="bg-blue-500 text-white text-xs rounded-full px-2 py-0.5">
                        {Object.values(filters).filter(v => v !== '').length}
                    </span>
                )}
            </button>

            {/* Filter Panel */}
            {isOpen && (
                <div className="absolute top-full mt-2 right-0 w-80 bg-white rounded-lg shadow-xl border border-gray-200 p-4 z-50">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="font-semibold text-gray-900">Search Filters</h3>
                        <button
                            onClick={() => setIsOpen(false)}
                            className="text-gray-400 hover:text-gray-600"
                        >
                            <X size={20} />
                        </button>
                    </div>

                    <div className="space-y-4">
                        {/* Bedrooms */}
                        <div>
                            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                                <Bed size={16} />
                                Bedrooms
                            </label>
                            <select
                                value={filters.bedrooms}
                                onChange={(e) => setFilters({ ...filters, bedrooms: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            >
                                <option value="">Any</option>
                                <option value="1">1+</option>
                                <option value="2">2+</option>
                                <option value="3">3+</option>
                                <option value="4">4+</option>
                                <option value="5">5+</option>
                            </select>
                        </div>

                        {/* Bathrooms */}
                        <div>
                            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                                <Bath size={16} />
                                Bathrooms
                            </label>
                            <select
                                value={filters.bathrooms}
                                onChange={(e) => setFilters({ ...filters, bathrooms: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            >
                                <option value="">Any</option>
                                <option value="1">1+</option>
                                <option value="2">2+</option>
                                <option value="3">3+</option>
                                <option value="4">4+</option>
                            </select>
                        </div>

                        {/* Price Range */}
                        <div>
                            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                                <DollarSign size={16} />
                                Price Range
                            </label>
                            <div className="grid grid-cols-2 gap-2">
                                <input
                                    type="number"
                                    placeholder="Min"
                                    value={filters.minPrice}
                                    onChange={(e) => setFilters({ ...filters, minPrice: e.target.value })}
                                    className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                                />
                                <input
                                    type="number"
                                    placeholder="Max"
                                    value={filters.maxPrice}
                                    onChange={(e) => setFilters({ ...filters, maxPrice: e.target.value })}
                                    className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                                />
                            </div>
                        </div>

                        {/* Home Type */}
                        <div>
                            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                                <Home size={16} />
                                Home Type
                            </label>
                            <select
                                value={filters.homeType}
                                onChange={(e) => setFilters({ ...filters, homeType: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            >
                                <option value="">Any Type</option>
                                <option value="Single Wide">Single Wide</option>
                                <option value="Double Wide">Double Wide</option>
                                <option value="Modular">Modular</option>
                            </select>
                        </div>
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-2 mt-4 pt-4 border-t border-gray-200">
                        <button
                            onClick={handleClear}
                            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
                        >
                            Clear All
                        </button>
                        <button
                            onClick={handleApply}
                            className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
                        >
                            Apply Filters
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
