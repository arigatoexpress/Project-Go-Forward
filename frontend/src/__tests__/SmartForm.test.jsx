import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SmartForm from '../components/SmartForm';

// Minimal fixture mirroring config/field_map.json shape: SmartForm fetches
// /api/documents/templates/{name}/fields and renders one input per entry.
const fieldFixture = {
  sections: {
    buyer: [
      {
        field_name: 'buyer_full_name',
        label: 'Buyer Full Name',
        type: 'text',
        required: true,
      },
      {
        field_name: 'buyer_email',
        label: 'Buyer Email',
        type: 'email',
        required: false,
      },
    ],
    pricing: [
      {
        field_name: 'sales_price',
        label: 'Sales Price',
        type: 'currency',
        required: true,
      },
    ],
  },
};

describe('SmartForm', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (typeof url === 'string' && url.includes('/api/documents/templates/')) {
        return {
          ok: true,
          json: async () => fieldFixture,
        };
      }
      // Pre-fill endpoint — return empty so SmartForm uses defaults.
      return {
        ok: true,
        json: async () => ({ extracted_data: {} }),
      };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders one input per field entry from the field_map', async () => {
    render(
      <SmartForm
        templateName="TMHA_SalesContract"
        title="Sales Contract"
        sessionId={null}
        onSubmit={() => {}}
        onCancel={() => {}}
      />,
    );

    // Wait for the loading state to clear and fields to render.
    await waitFor(() => {
      expect(screen.getByText('Buyer Full Name')).toBeInTheDocument();
    });

    // One input per entry across all sections (3 total in the fixture).
    expect(screen.getByText('Buyer Email')).toBeInTheDocument();
    expect(screen.getByText('Sales Price')).toBeInTheDocument();

    const inputs = document.querySelectorAll('input');
    // SmartForm may render auxiliary computed inputs (e.g. unpaid_balance),
    // but at minimum it must produce one input per fixture field.
    expect(inputs.length).toBeGreaterThanOrEqual(3);
  });
});
