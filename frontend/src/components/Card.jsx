import React from 'react';

/**
 * Card — Standardized container with elevation and hover states.
 */
export default function Card({ children, className = "", onClick, elevation = "sm" }) {
  const elevations = {
    none: "",
    sm: "shadow-sm hover:shadow-md",
    md: "shadow-md hover:shadow-lg",
    lg: "shadow-lg hover:shadow-xl",
  };

  return (
    <div 
      className={`bg-white rounded-2xl border border-slate-200 overflow-hidden transition-all duration-300 ${onClick ? 'cursor-pointer' : ''} ${elevations[elevation]} ${className}`}
      onClick={onClick}
    >
      {children}
    </div>
  );
}
