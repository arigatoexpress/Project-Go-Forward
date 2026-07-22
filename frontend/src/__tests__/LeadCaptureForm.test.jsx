import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LeadCaptureForm } from '../pages/InventoryBrowse';

// Regression guard for the silent-lead-loss bug: the quote/tour form used to
// check only resp.ok, but the backend returns HTTP 200 {success:false} on a
// validation or storage failure — so a dropped lead showed a success screen.

const home = { model_name: 'Sapphire 3-Bed', specs: { beds: 3, baths: 2 } };

function fillValidAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText('Your full name'), {
    target: { value: 'Alice Buyer' },
  });
  fireEvent.change(screen.getByPlaceholderText('(281) 000-0000'), {
    target: { value: '2813243020' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Get Quote/i }));
}

describe('LeadCaptureForm', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows an error and NOT a success screen when the backend returns {success:false}', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: false, error: 'Could not save your request' }),
    });

    render(<LeadCaptureForm home={home} type="quote" onClose={() => {}} />);
    fillValidAndSubmit();

    expect(await screen.findByText('Could not save your request')).toBeInTheDocument();
    expect(screen.queryByText('Thank You!')).not.toBeInTheDocument();
  });

  it('shows the success screen only when the backend returns {success:true}', async () => {
    global.fetch.mockResolvedValue({ ok: true, json: async () => ({ success: true }) });

    render(<LeadCaptureForm home={home} type="quote" onClose={() => {}} />);
    fillValidAndSubmit();

    expect(await screen.findByText('Thank You!')).toBeInTheDocument();
    const [, request] = global.fetch.mock.calls[0];
    expect(JSON.parse(request.body)).toMatchObject({
      source: 'inventory_quote',
      message: expect.stringContaining('Price Quote Request — Sapphire 3-Bed'),
    });
  });

  it('hands the persisted lead and home context to appointment booking', async () => {
    const onBookAppointment = vi.fn();
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, lead_id: 'contact_123_abcd' }),
    });

    render(
      <LeadCaptureForm
        home={{ ...home, home_id: 'home-42' }}
        type="quote"
        onClose={() => {}}
        onBookAppointment={onBookAppointment}
      />,
    );
    fillValidAndSubmit();
    fireEvent.click(await screen.findByRole('button', { name: /Choose a visit time/i }));

    expect(onBookAppointment).toHaveBeenCalledWith({
      name: 'Alice Buyer',
      phone: '2813243020',
      email: '',
      notes: 'Interested in Sapphire 3-Bed.',
      leadId: 'contact_123_abcd',
      source: 'inventory_quote_handoff',
      home: 'Sapphire 3-Bed',
      homeId: 'home-42',
      intent: 'quote',
    });
  });

  it('blocks submission of a sub-10-digit phone before calling the API', () => {
    render(<LeadCaptureForm home={home} type="quote" onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText('Your full name'), {
      target: { value: 'Alice Buyer' },
    });
    fireEvent.change(screen.getByPlaceholderText('(281) 000-0000'), {
      target: { value: '123' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Get Quote/i }));

    expect(screen.getByText('Please enter a valid 10-digit phone number.')).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
