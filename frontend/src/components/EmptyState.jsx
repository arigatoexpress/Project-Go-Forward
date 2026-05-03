import React from 'react';

/**
 * EmptyState — centered placeholder shown when a list/search yields no
 * results, when an error blocks loading, or when a feature requires a
 * one-time setup step. Replaces `.tho-browse-empty` / `.tho-browse-loading`
 * style patterns with a reusable component.
 *
 * Props:
 *   icon:        optional ReactNode (typically a lucide icon at size 36-48)
 *   iconWrapClassName: Tailwind classes for the colored disc behind the
 *                icon. Defaults to a subtle slate disc; pass e.g.
 *                "bg-red-50 text-red-400" for an error variant.
 *   title:       short headline (e.g. "No homes match your search")
 *   message:     supporting paragraph
 *   action:      optional primary button node (rendered below the message)
 *   secondary:   optional secondary action node (rendered to the right of
 *                or below the primary action depending on viewport)
 *   className:   extra Tailwind classes for the outer wrapper
 */
export default function EmptyState({
  icon,
  iconWrapClassName = 'bg-slate-100 text-slate-400',
  title,
  message,
  action,
  secondary,
  className = '',
}) {
  return (
    <div
      className={[
        'flex flex-col items-center justify-center text-center px-4 py-16',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {icon && (
        <div
          className={`flex items-center justify-center rounded-full p-5 mb-3 ${iconWrapClassName}`}
        >
          {icon}
        </div>
      )}
      {title && (
        <p className="text-gray-700 font-medium text-lg mb-1">{title}</p>
      )}
      {message && (
        <p className="text-gray-500 text-sm mb-4 max-w-md">{message}</p>
      )}
      {(action || secondary) && (
        <div className="flex flex-col sm:flex-row items-center gap-2">
          {action}
          {secondary}
        </div>
      )}
    </div>
  );
}
