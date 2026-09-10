import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import Contact from '../pages/Contact';
import { LeadCaptureForm } from '../pages/InventoryBrowse';

const { trackEvent } = vi.hoisted(() => ({ trackEvent: vi.fn() }));
vi.mock('../utils/analytics', () => ({ trackEvent }));

const failure = 'Unable to accept your request. Please try again or call us directly.';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  trackEvent.mockClear();
});

describe.each(['contact', 'quote'])('%s intake delivery failure', (form) => {
  it('shows the retryable error, preserves input, and accepts a successful retry', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({ success: false, error: failure }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ success: true, lead_id: 'synthetic-lead' }),
      });
    vi.stubGlobal('fetch', fetchMock);
    render(form === 'contact' ? <Contact /> : (
      <LeadCaptureForm
        home={{ home_id: 'synthetic-home', model_name: 'Synthetic Home' }}
        type="quote"
        onClose={() => {}}
      />
    ));
    const name = screen.getByPlaceholderText(form === 'contact' ? 'John Doe' : 'Your full name');
    const phone = screen.getByPlaceholderText('(281) 000-0000');
    fireEvent.change(name, { target: { value: 'Synthetic Lead' } });
    fireEvent.change(phone, { target: { value: '5125550123' } });
    if (form === 'contact') {
      fireEvent.change(screen.getByPlaceholderText('Tell us about the home you are looking for...'), {
        target: { value: 'Synthetic request for the acceptance test.' },
      });
    }
    const submit = screen.getByRole('button', { name: form === 'contact' ? 'Send Message' : 'Get Quote' });
    fireEvent.click(submit);

    expect(await screen.findByText(failure)).toBeInTheDocument();
    expect(name).toHaveValue('Synthetic Lead');
    expect(phone).toHaveValue('5125550123');
    expect(submit).toBeEnabled();
    expect(trackEvent).not.toHaveBeenCalledWith('lead_captured', expect.anything());

    fireEvent.click(submit);
    expect(await screen.findByText(form === 'contact' ? 'Message received' : 'Thank You!')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(trackEvent).toHaveBeenCalledWith('lead_captured', expect.anything());
  });
});
