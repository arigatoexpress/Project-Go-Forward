import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ChatCallbackCard from '../components/ChatCallbackCard';

describe('ChatCallbackCard', () => {
  beforeEach(() => {
    sessionStorage.clear();
    global.fetch = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: vi.fn(() => true),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('requires a valid phone before requesting a callback', () => {
    render(<ChatCallbackCard sessionId="session-1" onCaptured={() => {}} onDismiss={() => {}} />);

    fireEvent.change(screen.getByLabelText(/Your name/i), { target: { value: 'Maria' } });
    fireEvent.change(screen.getByLabelText(/Best callback number/i), { target: { value: '123' } });
    fireEvent.click(screen.getByRole('button', { name: /Request my callback/i }));

    expect(screen.getByText(/valid 10-digit phone/i)).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('brings the start of the handoff card into view on mobile', async () => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
    render(<ChatCallbackCard sessionId="session-1" onCaptured={() => {}} onDismiss={() => {}} />);

    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'start',
    }));
  });

  it('stores explicit callback consent and reports success', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, lead_id: 'lead-1' }),
    });
    const onCaptured = vi.fn();
    render(<ChatCallbackCard sessionId="session-1" onCaptured={onCaptured} onDismiss={() => {}} />);

    fireEvent.change(screen.getByLabelText(/Your name/i), { target: { value: 'Maria Buyer' } });
    fireEvent.change(screen.getByLabelText(/Best callback number/i), { target: { value: '281-324-3020' } });
    fireEvent.click(screen.getByRole('button', { name: /Request my callback/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    const [url, options] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/chat/contact');
    const payload = JSON.parse(options.body);
    expect(payload).toMatchObject({
      sessionId: 'session-1',
      name: 'Maria Buyer',
      phone: '281-324-3020',
      consent: true,
    });
    expect(payload.journey_id).toMatch(/^j_[0-9a-f]{32}$/);
    expect(await screen.findByText(/callback request is saved/i)).toBeInTheDocument();
    expect(onCaptured).toHaveBeenCalledWith('lead-1');
  });

  it('keeps the form actionable when storage fails', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      json: async () => ({ success: false, error: 'Could not save your callback request.' }),
    });
    render(<ChatCallbackCard sessionId="session-1" onCaptured={() => {}} onDismiss={() => {}} />);

    fireEvent.change(screen.getByLabelText(/Your name/i), { target: { value: 'Maria Buyer' } });
    fireEvent.change(screen.getByLabelText(/Best callback number/i), { target: { value: '2813243020' } });
    fireEvent.click(screen.getByRole('button', { name: /Request my callback/i }));

    expect(await screen.findByText(/Could not save your callback request/i)).toBeInTheDocument();
    expect(screen.queryByText(/callback request is saved/i)).not.toBeInTheDocument();
  });
});
