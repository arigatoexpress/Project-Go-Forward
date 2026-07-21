import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Contact from '../pages/Contact';
import Appointments from '../pages/Appointments';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('contact appointment handoff', () => {
  it('passes the persisted contact and message into appointment booking', async () => {
    const onBookAppointment = vi.fn();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, lead_id: 'contact_456_ef01' }),
    });

    render(<Contact onBack={() => {}} onBookAppointment={onBookAppointment} />);
    fireEvent.change(screen.getByLabelText('Your Name'), { target: { value: 'Ari Buyer' } });
    fireEvent.change(screen.getByLabelText('Phone Number'), { target: { value: '2813243020' } });
    fireEvent.change(screen.getByLabelText('Email (optional)'), { target: { value: 'ari@example.com' } });
    fireEvent.change(screen.getByLabelText('What can we help with?'), { target: { value: 'Looking for a 3 bedroom.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send Message' }));
    fireEvent.click(await screen.findByRole('button', { name: /Choose a visit time/i }));

    expect(onBookAppointment).toHaveBeenCalledWith({
      name: 'Ari Buyer',
      phone: '2813243020',
      email: 'ari@example.com',
      notes: 'Looking for a 3 bedroom.',
      leadId: 'contact_456_ef01',
      source: 'contact_handoff',
      intent: 'contact',
    });
  });
});

describe('appointment prefill', () => {
  it('keeps PII in component state and submits the bound lead id', async () => {
    const prefill = {
      name: 'Ari Buyer',
      phone: '2813243020',
      email: 'ari@example.com',
      notes: 'Interested in Sapphire 3-Bed.',
      leadId: 'contact_123_abcd',
      source: 'inventory_quote_handoff',
      home: 'Sapphire 3-Bed',
      intent: 'quote',
    };
    global.fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ available_slots: ['10:00 AM'], business_hours: '9–5' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ success: true, appointment: { appointment_id: 'appt_test' } }),
      });

    const { container } = render(<Appointments onBack={() => {}} prefill={prefill} />);
    const availableDate = [...container.querySelectorAll('button')]
      .find((button) => /^\d+$/.test(button.textContent) && !button.disabled);
    fireEvent.click(availableDate);
    fireEvent.click(await screen.findByRole('button', { name: /10:00 AM/i }));

    expect(screen.getByLabelText('Full Name *')).toHaveValue('Ari Buyer');
    expect(screen.getByLabelText('Phone Number *')).toHaveValue('2813243020');
    expect(screen.getByLabelText('Email (optional)')).toHaveValue('ari@example.com');
    expect(screen.getByLabelText('What homes are you interested in?')).toHaveValue('Interested in Sapphire 3-Bed.');

    fireEvent.click(screen.getByRole('button', { name: 'Review & Confirm' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm Appointment/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
    const [, request] = global.fetch.mock.calls[1];
    expect(JSON.parse(request.body)).toMatchObject({
      name: 'Ari Buyer',
      phone: '2813243020',
      lead_id: 'contact_123_abcd',
      source: 'inventory_quote_handoff',
    });
  });
});
