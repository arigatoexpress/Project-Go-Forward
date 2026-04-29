import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, Lock } from 'lucide-react';

const SECTION_LABELS = {
  buyer: 'Buyer Information',
  seller: 'Seller / Dealer',
  home: 'Home Details',
  pricing: 'Pricing',
  financing: 'Financing',
  creditor: 'Creditor / Lender',
  insurance: 'Insurance & Escrow',
  transaction: 'Transaction Details',
  other: 'Other',
};

const SECTION_ORDER = ['buyer', 'seller', 'home', 'pricing', 'financing', 'creditor', 'insurance', 'transaction', 'other'];
const EMPTY_INITIAL_DATA = {};

const SmartForm = ({ templateName, title, sessionId, onSubmit, onCancel, initialData = EMPTY_INITIAL_DATA }) => {
  const [sections, setSections] = useState({});
  const [formData, setFormData] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [locked, setLocked] = useState(false);
  const [preFilling, setPreFilling] = useState(false);

  const tryPreFill = useCallback(async () => {
    setPreFilling(true);
    try {
      const response = await fetch('/api/documents/extract-fields', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, template_name: templateName }),
      });
      if (response.ok) {
        const data = await response.json();
        if (data.extracted_data) {
          setFormData(prev => ({ ...prev, ...data.extracted_data }));
        }
      }
    } catch {
      // Pre-fill is best-effort, don't block on failure
    } finally {
      setPreFilling(false);
    }
  }, [sessionId, templateName]);

  const fetchFieldDefinitions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/documents/templates/${encodeURIComponent(templateName)}/fields`);
      const data = await response.json();

      if (data.error) {
        setError(data.error);
        return;
      }

      setSections(data.sections || {});

      // Initialize form data with defaults
      const defaults = {};
      Object.values(data.sections || {}).forEach((fields) => {
        fields.forEach(field => {
          if (field.default !== null && field.default !== undefined) {
            defaults[field.field_name] = field.default;
          } else if (field.type === 'currency') {
            defaults[field.field_name] = '';
          } else if (field.type === 'checkbox') {
            defaults[field.field_name] = false;
          } else {
            defaults[field.field_name] = '';
          }
        });
      });
      setFormData({ ...defaults, ...initialData });

      // Try pre-filling from chat session
      if (sessionId) {
        tryPreFill();
      }
    } catch (e) {
      setError('Failed to load form fields: ' + e.message);
    } finally {
      setLoading(false);
    }
  }, [initialData, sessionId, templateName, tryPreFill]);

  useEffect(() => {
    fetchFieldDefinitions();
  }, [fetchFieldDefinitions]);

  const handleChange = (fieldName, value) => {
    if (locked) return;
    setFormData(prev => {
      const updated = { ...prev, [fieldName]: value };

      // Auto-compute unpaid_balance
      if (fieldName === 'sales_price' || fieldName === 'down_payment') {
        const price = parseFloat(updated.sales_price) || 0;
        const down = parseFloat(updated.down_payment) || 0;
        updated.unpaid_balance = Math.max(0, price - down).toString();
        updated.total_unpaid_balance = updated.unpaid_balance;
      }

      // Auto-compute buyer_city_state_zip
      if (fieldName === 'buyer_city' || fieldName === 'buyer_state' || fieldName === 'buyer_zip') {
        const city = updated.buyer_city || '';
        const state = updated.buyer_state || 'TX';
        const zip = updated.buyer_zip || '';
        updated.buyer_city_state_zip = `${city}, ${state} ${zip}`.trim();
      }

      return updated;
    });
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!locked) {
      setLocked(true);
      return;
    }
    onSubmit(formData);
  };

  const renderField = (field) => {
    const { field_name, label, type, required, computed } = field;
    const value = formData[field_name] ?? '';

    const inputClass = `mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm ${
      locked ? 'bg-gray-50 cursor-not-allowed' : ''
    } ${computed ? 'bg-gray-50' : ''}`;

    if (type === 'checkbox') {
      return (
        <div key={field_name} className="flex items-center">
          <input
            type="checkbox"
            id={field_name}
            checked={!!value}
            onChange={(e) => handleChange(field_name, e.target.checked)}
            disabled={locked}
            className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
          />
          <label htmlFor={field_name} className="ml-2 text-sm text-gray-700">{label}</label>
        </div>
      );
    }

    if (type === 'ssn') {
      return (
        <div key={field_name}>
          <label className="block text-sm font-medium text-gray-700">
            {label} {required && <span className="text-red-500">*</span>}
            <span className="text-xs text-red-400 ml-1">(PII - Manual entry only)</span>
          </label>
          <input
            type="password"
            name={field_name}
            value={value}
            onChange={(e) => handleChange(field_name, e.target.value)}
            required={required}
            disabled={locked}
            placeholder="***-**-****"
            maxLength={11}
            className={inputClass}
          />
        </div>
      );
    }

    if (type === 'currency') {
      return (
        <div key={field_name}>
          <label className="block text-sm font-medium text-gray-700">
            {label} {required && <span className="text-red-500">*</span>}
          </label>
          <div className="relative mt-1 rounded-md shadow-sm">
            <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
              <span className="text-gray-500 sm:text-sm">$</span>
            </div>
            <input
              type="number"
              name={field_name}
              value={value}
              onChange={(e) => handleChange(field_name, e.target.value)}
              required={required}
              readOnly={computed || locked}
              className={`block w-full rounded-md border-gray-300 pl-7 pr-4 focus:border-blue-500 focus:ring-blue-500 sm:text-sm ${
                locked || computed ? 'bg-gray-50 cursor-not-allowed' : ''
              }`}
              placeholder="0.00"
              step="0.01"
            />
          </div>
        </div>
      );
    }

    if (type === 'date') {
      return (
        <div key={field_name}>
          <label className="block text-sm font-medium text-gray-700">
            {label} {required && <span className="text-red-500">*</span>}
          </label>
          <input
            type="date"
            name={field_name}
            value={value}
            onChange={(e) => handleChange(field_name, e.target.value)}
            required={required}
            disabled={locked}
            className={inputClass}
          />
        </div>
      );
    }

    if (type === 'phone') {
      return (
        <div key={field_name}>
          <label className="block text-sm font-medium text-gray-700">
            {label} {required && <span className="text-red-500">*</span>}
          </label>
          <input
            type="tel"
            name={field_name}
            value={value}
            onChange={(e) => handleChange(field_name, e.target.value)}
            required={required}
            disabled={locked}
            className={inputClass}
          />
        </div>
      );
    }

    if (type === 'email') {
      return (
        <div key={field_name}>
          <label className="block text-sm font-medium text-gray-700">
            {label} {required && <span className="text-red-500">*</span>}
          </label>
          <input
            type="email"
            name={field_name}
            value={value}
            onChange={(e) => handleChange(field_name, e.target.value)}
            required={required}
            disabled={locked}
            className={inputClass}
          />
        </div>
      );
    }

    // Default: text input
    return (
      <div key={field_name}>
        <label className="block text-sm font-medium text-gray-700">
          {label} {required && <span className="text-red-500">*</span>}
        </label>
        <input
          type="text"
          name={field_name}
          value={value}
          onChange={(e) => handleChange(field_name, e.target.value)}
          required={required}
          readOnly={computed || locked}
          disabled={locked && !computed}
          className={inputClass}
        />
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600 mb-4" />
        <p className="text-gray-600">Loading form fields...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 bg-white rounded-lg shadow-md text-center">
        <p className="text-red-600 mb-4">{error}</p>
        <button onClick={onCancel} className="px-4 py-2 bg-gray-200 rounded-md hover:bg-gray-300">
          Go Back
        </button>
      </div>
    );
  }

  const orderedSections = SECTION_ORDER.filter(s => sections[s] && sections[s].length > 0);

  return (
    <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 rounded-lg shadow-md max-w-4xl mx-auto">
      <div className="border-b border-gray-200 pb-4 mb-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-800">{title || 'Document Form'}</h2>
            <p className="text-sm text-gray-500">
              {locked ? 'Review your data, then click Generate to create the PDF.' : 'Fill in the fields below to generate this document.'}
            </p>
          </div>
          {preFilling && (
            <div className="flex items-center text-blue-600 text-sm">
              <Loader2 className="h-4 w-4 animate-spin mr-1" />
              Pre-filling from chat...
            </div>
          )}
        </div>
      </div>

      {orderedSections.map(sectionKey => {
        const fields = sections[sectionKey];
        const checkboxFields = fields.filter(f => f.type === 'checkbox');
        const regularFields = fields.filter(f => f.type !== 'checkbox');

        return (
          <div key={sectionKey} className="border-t border-gray-200 pt-4">
            <h3 className="text-lg font-medium text-gray-900 mb-3">{SECTION_LABELS[sectionKey] || sectionKey}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {regularFields.map(field => renderField(field))}
            </div>
            {checkboxFields.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-4">
                {checkboxFields.map(field => renderField(field))}
              </div>
            )}
          </div>
        );
      })}

      <div className="flex justify-end space-x-3 pt-6 border-t border-gray-200">
        <button
          type="button"
          onClick={locked ? () => setLocked(false) : onCancel}
          className="bg-white py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          {locked ? 'Unlock & Edit' : 'Cancel'}
        </button>
        <button
          type="submit"
          className="inline-flex items-center justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700"
        >
          {locked ? (
            <>Generate PDF</>
          ) : (
            <><Lock size={16} className="mr-1" /> Lock & Review</>
          )}
        </button>
      </div>
    </form>
  );
};

export default SmartForm;
