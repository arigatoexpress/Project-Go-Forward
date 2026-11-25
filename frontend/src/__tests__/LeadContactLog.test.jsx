import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import LeadResponseQueue, {
  CONTACT_OUTCOMES,
  contactAttemptLine,
  smsHref,
} from '../components/LeadResponseQueue';
import { submitLeadContactLog } from '../pages/CRM';

const NOW = new Date('2026-11-25T16:00:00Z');

function lead(overrides = {}) {
  return {
    lead_id: 'lead-1',
    name: 'M. L.',
    phone: '(281) 555-0123',
    email: 'ml@example.com',
    status: 'new',
    created_at: '2026-11-25T15:50:00Z',
    homes_viewed: [],
    ...overrides,
  };
}

describe('one-tap contact logging (COD-254)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('offers the five outcomes, a Text link, and hides the old single button', () => {
    render(<LeadResponseQueue leads={[lead()]} onLogContact={vi.fn()} onMarkContacted={vi.fn()} />);
    const group = screen.getByRole('group', { name: 'Log contact with M. L.' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(
      ['Called', 'Texted', 'Emailed', 'Left voicemail', 'No answer'],
    );
    expect(screen.getByRole('link', { name: 'Text M. L.' })).toHaveAttribute('href', 'sms:2815550123');
    expect(screen.queryByText('I already talked to them')).toBeNull();
  });

  it('keeps the old button when the page does not wire contact logging', () => {
    render(<LeadResponseQueue leads={[lead()]} onMarkContacted={vi.fn()} />);
    expect(screen.getByText('I already talked to them')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /Log contact/ })).toBeNull();
  });

  it('one tap saves the outcome and tells staff what happens next', async () => {
    const onLogContact = vi.fn().mockResolvedValue(true);
    render(<LeadResponseQueue leads={[lead()]} onLogContact={onLogContact} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'No answer: M. L.' })); });
    expect(onLogContact).toHaveBeenCalledWith('lead-1', 'no_answer');
    expect(screen.getByRole('status')).toHaveTextContent('stays on this list');

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Called: M. L.' })); });
    expect(onLogContact).toHaveBeenLastCalledWith('lead-1', 'called');
    expect(screen.getByRole('status')).toHaveTextContent('marked as answered');
  });

  it('says plainly when a tap was not saved', async () => {
    render(<LeadResponseQueue leads={[lead()]} onLogContact={vi.fn().mockResolvedValue(false)} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Texted: M. L.' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('not saved');
  });

  it('shows earlier attempts and the assigned owner on the card', () => {
    render(
      <LeadResponseQueue
        leads={[lead({
          contact_attempts: 2,
          last_contact_outcome: 'left_voicemail',
          last_contact_at: '2026-11-25T15:30:00',
          assigned_to: 'sales-desk',
        })]}
        onLogContact={vi.fn()}
      />,
    );
    expect(screen.getByTestId('contact-attempts')).toHaveTextContent('Tried 2× · last: Left voicemail 30m ago');
    expect(screen.getByText(/Assigned to sales-desk/)).toBeInTheDocument();
  });
});

describe('helpers', () => {
  it('matches the server outcome list', () => {
    expect(CONTACT_OUTCOMES.map((o) => o.value)).toEqual(
      ['called', 'texted', 'emailed', 'left_voicemail', 'no_answer'],
    );
    expect(CONTACT_OUTCOMES.filter((o) => o.reached).map((o) => o.value)).toEqual(['called', 'texted', 'emailed']);
  });

  it('builds sms links from messy numbers and skips empty ones', () => {
    expect(smsHref('+1 (281) 555-0123')).toBe('sms:+12815550123');
    expect(smsHref('')).toBe('');
  });

  it('has no attempt line before the first attempt', () => {
    expect(contactAttemptLine(lead(), NOW)).toBe('');
  });

  it('posts only the outcome and surfaces server errors', async () => {
    const ok = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ success: true, lead: { lead_id: 'lead-1' } }) });
    await expect(submitLeadContactLog(ok, 'lead-1', 'called')).resolves.toEqual({ lead_id: 'lead-1' });
    expect(ok).toHaveBeenCalledWith('/api/leads/lead-1/contact-log', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ outcome: 'called' }),
    }));
    const bad = vi.fn().mockResolvedValue({ ok: false, status: 400, json: async () => ({ detail: 'Unknown contact outcome.' }) });
    await expect(submitLeadContactLog(bad, 'lead-1', 'x')).rejects.toThrow('Unknown contact outcome.');
  });
});
