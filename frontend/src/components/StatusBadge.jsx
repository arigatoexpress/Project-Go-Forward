import React from 'react';

/**
 * StatusBadge — Consistent chip for home status or categories.
 */
export default function StatusBadge({ children, variant = "default", className = "" }) {
  const base = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200";
  
  const variants = {
    new: "bg-green-500 text-white shadow-sm shadow-green-100",
    used: "bg-amber-500 text-white shadow-sm shadow-amber-100",
    tour: "bg-slate-900/80 text-white backdrop-blur-md",
    kitchen: "bg-amber-100 text-amber-800",
    bedroom: "bg-blue-100 text-blue-800",
    bathroom: "bg-emerald-100 text-emerald-800",
    default: "bg-slate-100 text-slate-600",
  };

  const activeVariant = variants[variant.toLowerCase()] || variants.default;

  return (
    <span className={`${base} ${activeVariant} ${className}`}>
      {children}
    </span>
  );
}
