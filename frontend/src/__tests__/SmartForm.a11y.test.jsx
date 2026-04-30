import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SmartForm from '../components/SmartForm';

// Verifies that every field rendered by SmartForm has an associated <label>
// (htmlFor / id pair) so screen readers and getByLabelText resolve correctly.
const fixture = {
  sections: {
    buyer: [
      { field_name: 'buyer_full_name', label: 'Buyer Full Name', type: 'text', required: true },
      { field_name: 'buyer_email', label: 'Buyer Email', type: 'email', required: false },
      { field_name: 'buyer_phone', label: 'Buyer Phone', type: 'phone', required: true },
    ],
    pricing: [
      { field_name: 'sales_price', label: 'Sales Price', type: 'currency', required: true },
      { field_name: 'closing_date', label: 'Closing Date', type: 'date', required: false },
    ],
    transaction: [
      { field_name: 'cash_purchase', label: 'Cash purchase', type: 'checkbox', required: false },
    ],
  },
};

describe('SmartForm a11y', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (typeof url === 'string' && url.includes('/api/documents/templates/')) {
        return { ok: true, json: async () => fixture };
      }
      return { ok: true, json: async () => ({ extracted_data: {} }) };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('associates a <label> with every input via htmlFor / id', async () => {
    render(
      <SmartForm
        templateName="TMHA_SalesContract"
        title="Sales Contract"
        sessionId={null}
        onSubmit={() => {}}
        onCancel={() => {}}
      />
    );

    // Wait for fields to mount.
    await waitFor(() => screen.getByLabelText(/buyer full name/i));

    expect(screen.getByLabelText(/buyer full name/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/buyer email/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/buyer phone/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/sales price/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/closing date/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/cash purchase/i)).toBeInstanceOf(HTMLInputElement);
  });

  it('marks required fields with aria-required="true"', async () => {
    render(
      <SmartForm
        templateName="TMHA_SalesContract"
        title="Sales Contract"
        sessionId={null}
        onSubmit={() => {}}
        onCancel={() => {}}
      />
    );

    await waitFor(() => screen.getByLabelText(/buyer full name/i));

    expect(screen.getByLabelText(/buyer full name/i)).toHaveAttribute('aria-required', 'true');
    expect(screen.getByLabelText(/buyer phone/i)).toHaveAttribute('aria-required', 'true');
    expect(screen.getByLabelText(/sales price/i)).toHaveAttribute('aria-required', 'true');
    // Optional fields should not carry aria-required.
    expect(screen.getByLabelText(/buyer email/i)).not.toHaveAttribute('aria-required');
    expect(screen.getByLabelText(/closing date/i)).not.toHaveAttribute('aria-required');
  });
});
