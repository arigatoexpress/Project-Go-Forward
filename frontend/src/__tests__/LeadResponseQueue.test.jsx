import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import LeadResponseQueue, {
  getLeadResponseState,
  parseLeadCreatedAt,
  selectResponseQueue,
} from '../components/LeadResponseQueue';

const NOW = new Date('2026-07-21T18:00:00Z');

function lead(overrides = {}) {
  return {
    lead_id: 'lead-1',
    name: 'Maria Lopez',
    phone: '+12815550123',
    email: 'maria@example.com',
    source: 'contact_form',
    status: 'new',
    created_at: '2026-07-21T17:55:00Z',
    homes_viewed: [],
    appointment_requested: false,
    ...overrides,
  };
}

describe('speed-to-lead queue selection', () => {
  it('keeps only reachable new leads and puts appointment intent first', () => {
    const selected = selectResponseQueue([
      lead({ lead_id: 'unreachable', phone: '', email: '' }),
      lead({ lead_id: 'contacted', status: 'contacted' }),
      lead({ lead_id: 'regular', created_at: '2026-07-21T17:00:00Z' }),
      lead({ lead_id: 'appointment', appointment_requested: true, created_at: '2026-07-21T17:58:00Z' }),
    ], NOW);

    expect(selected.map((item) => item.lead_id)).toEqual(['appointment', 'regular']);
  });

  it('puts overdue leads ahead of waiting and fresh leads within the same intent tier', () => {
    const selected = selectResponseQueue([
      lead({ lead_id: 'fresh', created_at: '2026-07-21T17:55:00Z' }),
      lead({ lead_id: 'waiting', created_at: '2026-07-21T17:30:00Z' }),
      lead({ lead_id: 'overdue', created_at: '2026-07-21T15:00:00Z' }),
      lead({ lead_id: 'appointment', appointment_requested: true, created_at: '2026-07-21T17:58:00Z' }),
    ], NOW);

    expect(selected.map((item) => item.lead_id))
      .toEqual(['appointment', 'overdue', 'waiting', 'fresh']);
  });

  it('treats timezone-naive backend timestamps as UTC', () => {
    expect(parseLeadCreatedAt('2026-07-21T17:55:00').toISOString())
      .toBe('2026-07-21T17:55:00.000Z');
  });

  it('labels fresh, waiting, and overdue leads against the response clock', () => {
    expect(getLeadResponseState(lead(), NOW).key).toBe('fresh');
    expect(getLeadResponseState(lead({ created_at: '2026-07-21T17:30:00Z' }), NOW).key)
      .toBe('waiting');
    expect(getLeadResponseState(lead({ created_at: '2026-07-21T15:00:00Z' }), NOW).key)
      .toBe('overdue');
    expect(getLeadResponseState(lead({ created_at: '2026-07-21T17:00:00Z' }), NOW).key)
      .toBe('waiting');
    expect(getLeadResponseState(lead({ created_at: '2026-07-21T16:59:00Z' }), NOW).key)
      .toBe('overdue');
  });
});

describe('LeadResponseQueue', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders one-tap contact actions and marks a lead contacted', async () => {
    const onMarkContacted = vi.fn().mockResolvedValue(undefined);
    render(
      <LeadResponseQueue
        leads={[lead()]}
        onOpen={() => {}}
        onMarkContacted={onMarkContacted}
      />,
    );

    expect(screen.getByRole('heading', { name: /Respond now/i })).toBeTruthy();
    expect(screen.getByText(/15-minute target/i)).toBeTruthy();
    expect(screen.getByRole('link', { name: /Call Maria Lopez/i }).getAttribute('href'))
      .toBe('tel:+12815550123');
    expect(screen.getByRole('link', { name: /Email Maria Lopez/i }).getAttribute('href'))
      .toBe('mailto:maria@example.com');

    fireEvent.click(screen.getByRole('button', { name: /Mark Maria Lopez contacted/i }));
    await Promise.resolve();
    expect(onMarkContacted).toHaveBeenCalledWith('lead-1');
  });

  it('does not render when no reachable new lead needs a response', () => {
    const { container } = render(
      <LeadResponseQueue
        leads={[lead({ status: 'contacted' }), lead({ phone: '', email: '' })]}
        onOpen={() => {}}
        onMarkContacted={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('advances the response clock while the CRM stays open', () => {
    render(
      <LeadResponseQueue
        leads={[lead({ created_at: '2026-07-21T17:45:00Z' })]}
        onOpen={() => {}}
        onMarkContacted={() => {}}
      />,
    );
    expect(screen.getByText(/Fresh · 15m waiting/i)).toBeTruthy();

    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText(/Waiting · 16m waiting/i)).toBeTruthy();
  });
});
