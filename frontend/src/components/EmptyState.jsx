import React from 'react';

/**
 * EmptyState — UI for zero-result or error conditions.
 */
export default function EmptyState({ icon: Icon, title, description, children, className = "" }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center py-16 px-6 rounded-3xl bg-slate-50 border-2 border-dashed border-slate-200 ${className}`}>
      {Icon && <Icon size={48} className="text-slate-300 mb-4" aria-hidden="true" />}
      {title && <h3 className="text-xl font-bold text-slate-900 mb-2">{title}</h3>}
      {description && <p className="text-slate-500 max-w-sm mx-auto mb-6">{description}</p>}
      {children}
    </div>
  );
}
