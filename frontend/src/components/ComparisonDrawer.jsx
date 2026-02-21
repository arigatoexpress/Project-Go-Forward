import React from 'react';
import { X, Check, Trash2 } from 'lucide-react';

const ComparisonDrawer = ({ isOpen, onClose, properties, onRemove }) => {
    if (!isOpen || properties.length === 0) return null;

    return (
        <div className="fixed inset-x-0 bottom-0 z-50 transform transition-transform duration-300 ease-in-out bg-white shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.1)] border-t border-gray-200 rounded-t-xl max-h-[80vh] flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-gray-100">
                <div className="flex items-center">
                    <h3 className="font-bold text-lg text-gray-800">Compare Homes</h3>
                    <span className="ml-2 bg-blue-100 text-blue-800 text-xs px-2 py-0.5 rounded-full font-medium">
                        {properties.length} / 3
                    </span>
                </div>
                <button
                    onClick={onClose}
                    className="p-1 rounded-full hover:bg-gray-100 text-gray-500 transition-colors"
                >
                    <X size={20} />
                </button>
            </div>

            {/* Content */}
            <div className="overflow-x-auto p-4 flex-1">
                <table className="w-full min-w-[600px] border-collapse">
                    <thead>
                        <tr>
                            <th className="p-2 text-left w-32 bg-gray-50 rounded-tl-lg">Feature</th>
                            {properties.map(p => (
                                <th key={p.id} className="p-2 bg-gray-50 text-center relative group min-w-[180px]">
                                    <div className="font-bold text-gray-900">{p.model_name}</div>
                                    <div className="text-xs text-gray-500 font-normal">{p.classification}</div>
                                    <button
                                        onClick={() => onRemove(p.id)}
                                        className="absolute top-1 right-1 p-1 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                                        title="Remove from comparison"
                                    >
                                        <Trash2 size={14} />
                                    </button>
                                </th>
                            ))}
                            {/* Fill empty slots */}
                            {[...Array(3 - properties.length)].map((_, i) => (
                                <th key={`empty-${i}`} className="p-2 bg-gray-50/50 text-center min-w-[180px]">
                                    <div className="text-gray-300 italic text-sm">Add another home<br />to compare</div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody className="text-sm">
                        {/* Price Row */}
                        <tr className="border-b border-gray-100">
                            <td className="p-3 font-medium text-gray-600">Price</td>
                            {properties.map(p => (
                                <td key={p.id} className="p-3 text-center font-bold text-blue-600 bg-blue-50/30">
                                    {p.pricing.display_price}
                                </td>
                            ))}
                            {[...Array(3 - properties.length)].map((_, i) => <td key={i}></td>)}
                        </tr>

                        {/* Specs Rows */}
                        <tr className="border-b border-gray-100">
                            <td className="p-3 font-medium text-gray-600">Bedrooms</td>
                            {properties.map(p => (
                                <td key={p.id} className="p-3 text-center">{p.specs.beds}</td>
                            ))}
                            {[...Array(3 - properties.length)].map((_, i) => <td key={i}></td>)}

                        </tr>
                        <tr className="border-b border-gray-100">
                            <td className="p-3 font-medium text-gray-600">Bathrooms</td>
                            {properties.map(p => (
                                <td key={p.id} className="p-3 text-center">{p.specs.baths}</td>
                            ))}
                            {[...Array(3 - properties.length)].map((_, i) => <td key={i}></td>)}

                        </tr>
                        <tr className="border-b border-gray-100">
                            <td className="p-3 font-medium text-gray-600">Size</td>
                            {properties.map(p => (
                                <td key={p.id} className="p-3 text-center">{p.specs.sq_ft} sq ft</td>
                            ))}
                            {[...Array(3 - properties.length)].map((_, i) => <td key={i}></td>)}

                        </tr>
                        <tr className="border-b border-gray-100">
                            <td className="p-3 font-medium text-gray-600">Manufacturer</td>
                            {properties.map(p => (
                                <td key={p.id} className="p-3 text-center text-gray-500">{p.manufacturer}</td>
                            ))}
                            {[...Array(3 - properties.length)].map((_, i) => <td key={i}></td>)}

                        </tr>
                    </tbody>
                </table>
            </div>

        </div>
    );
};

export default ComparisonDrawer;
