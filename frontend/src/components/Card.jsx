import React from 'react';

/**
 * Card — generic white card wrapper with rounded corners, soft border, and
 * optional hover lift. Replaces ad-hoc `.tho-home-card` / `.tho-detail-modal`
 * style patterns with a single reusable shell so future pages (Analytics,
 * ChatHistory) can adopt it without re-implementing the surface treatment.
 *
 * Pattern reference: PropertyCard.jsx (the standard hover-lift card used in
 * the chat property feed) — same border + shadow + rounded-xl conventions.
 *
 * Props:
 *   children:  card body
 *   padded:    when true (default), apply p-4; pass false for image-flush
 *              cards that manage their own padding zones
 *   hover:     when true, add lift-on-hover (shadow + translate-y)
 *   onClick:   passed through; sets cursor-pointer when provided
 *   className: extra Tailwind classes appended to the base chain
 */
export default function Card({
  children,
  padded = true,
  hover = false,
  onClick,
  className = '',
  ...rest
}) {
  const base = 'bg-white rounded-xl border border-gray-200 overflow-hidden';
  const padding = padded ? 'p-4' : '';
  const hoverCls = hover
    ? 'transition-all duration-200 hover:shadow-lg hover:-translate-y-0.5'
    : '';
  const clickable = onClick ? 'cursor-pointer' : '';

  return (
    <div
      onClick={onClick}
      className={[base, padding, hoverCls, clickable, className].filter(Boolean).join(' ')}
      {...rest}
    >
      {children}
    </div>
  );
}
