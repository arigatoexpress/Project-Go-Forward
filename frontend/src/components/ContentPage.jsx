import React from 'react';
import { ArrowLeft } from 'lucide-react';
import { BUSINESS_NAME } from '../constants';

/**
 * Shared wrapper for public trust/content pages.
 * Provides consistent max-width, spacing, back navigation, and page title.
 */
const ContentPage = ({ title, subtitle, children, onBack }) => (
  <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
    <div className="mb-8 flex items-center justify-between gap-4">
      <div>
        <h1 className="text-3xl font-bold text-blue-900">{title}</h1>
        {subtitle && <p className="text-gray-600 mt-1">{subtitle}</p>}
      </div>
      {onBack && (
        <button
          onClick={onBack}
          className="shrink-0 text-sm font-medium text-blue-600 hover:text-blue-800 inline-flex items-center gap-1"
        >
          <ArrowLeft size={16} />
          Back
        </button>
      )}
    </div>

    <article className="bg-white rounded-2xl shadow-xl border border-gray-100 p-6 sm:p-10">
      {children}
    </article>

    <p className="text-center text-xs text-gray-400 mt-8">
      © {new Date().getFullYear()} {BUSINESS_NAME}. All rights reserved.
    </p>
  </div>
);

export default ContentPage;
