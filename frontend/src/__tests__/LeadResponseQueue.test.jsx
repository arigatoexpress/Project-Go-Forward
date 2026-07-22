import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import LeadResponseQueue, {
  getLeadResponseState,
  parseLeadCreatedAt,
  selectResponseQueue,
  selectResponseQueues,
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
  it('keeps a brand-new lead ahead of 44 stale records and isolates recovery newest-first', () => {
    const staleLeads = Array.from({ length: 44 }, (_, index) => lead({
      lead_id: `stale-${index}`,
      name: `Stale ${index}`,
      appointment_requested: index < 7,
      created_at: new Date(NOW.getTime() - ((48 + index) * 60 * 60 * 1000)).toISOString(),
    }));
    const { active, recovery } = selectResponseQueues([
      ...staleLeads,
      lead({ lead_id: 'fresh-regular', name: 'Fresh Regular', created_at: '2026-07-21T17:55:00Z' }),
    ], NOW);

    expect(active.map((item) => item.lead_id)).toEqual(['fresh-regular']);
    expect(recovery).toHaveLength(44);
    expect(recovery.slice(0, 3).map((item) => item.lead_id))
      .toEqual(['stale-0', 'stale-1', 'stale-2']);
  });

  it('uses a strict 24-hour boundary and sends invalid timestamps to recovery', () => {
    const { active, recovery } = selectResponseQueues([
      lead({ lead_id: 'inside', created_at: '2026-07-20T18:01:00Z' }),
      lead({ lead_id: 'boundary', created_at: '2026-07-20T18:00:00' }),
      lead({ lead_id: 'outside', created_at: '2026-07-20T17:59:00Z' }),
      lead({ lead_id: 'invalid', created_at: 'not-a-timestamp' }),
    ], NOW);

    expect(active.map((item) => item.lead_id)).toEqual(['boundary', 'inside']);
    expect(recovery.map((item) => item.lead_id)).toEqual(['outside', 'invalid']);
  });

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

  it('puts explicit callback requests immediately behind appointments', () => {
    const selected = selectResponseQueue([
      lead({ lead_id: 'overdue', created_at: '2026-07-20T15:00:00Z' }),
      lead({ lead_id: 'callback', triage_reason: 'callback_requested', created_at: '2026-07-21T17:58:00Z' }),
      lead({ lead_id: 'appointment', appointment_requested: true, created_at: '2026-07-21T17:59:00Z' }),
    ], NOW);

    expect(selected.map((item) => item.lead_id))
      .toEqual(['appointment', 'callback', 'overdue']);
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
    const callLink = screen.getByRole('link', { name: /Call Maria Lopez/i });
    const emailLink = screen.getByRole('link', { name: /Email Maria Lopez/i });
    expect(callLink.getAttribute('href'))
      .toBe('tel:+12815550123');
    expect(emailLink.getAttribute('href'))
      .toBe('mailto:maria@example.com');
    fireEvent.click(callLink);
    fireEvent.click(emailLink);
    expect(onMarkContacted).not.toHaveBeenCalled();

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

  it('renders recent leads in Respond now and stale leads only in Recovery backlog', () => {
    const staleLeads = Array.from({ length: 44 }, (_, index) => lead({
      lead_id: `stale-${index}`,
      name: `Stale ${index}`,
      appointment_requested: index < 7,
      created_at: new Date(NOW.getTime() - ((48 + index) * 60 * 60 * 1000)).toISOString(),
    }));

    render(
      <LeadResponseQueue
        leads={[
          ...staleLeads,
          lead({ lead_id: 'fresh-regular', name: 'Fresh Regular', created_at: '2026-07-21T17:55:00Z' }),
        ]}
        onOpen={() => {}}
        onMarkContacted={() => {}}
      />,
    );

    const activeQueue = screen.getByTestId('active-response-queue');
    const recoveryQueue = screen.getByTestId('recovery-lead-queue');
    expect(within(activeQueue).getByText('Fresh Regular')).toBeTruthy();
    expect(within(activeQueue).queryByText('Stale 0')).toBeNull();
    expect(within(recoveryQueue).getByText('Stale 0')).toBeTruthy();
    expect(within(recoveryQueue).queryByText('Fresh Regular')).toBeNull();
    expect(screen.getByText(/1 lead needs a response now/i)).toBeTruthy();
    expect(screen.getByText(/44 older reachable leads/i)).toBeTruthy();
  });
});
