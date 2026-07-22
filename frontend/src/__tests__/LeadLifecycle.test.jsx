import { describe, expect, it, vi } from 'vitest';
import {
  formatLeadResponseTime,
  submitLeadLifecycleTransition,
} from '../pages/CRM';

describe('lead lifecycle client', () => {
  it('uses the dedicated PATCH endpoint and trusts the server lifecycle record', async () => {
    const serverLead = {
      lead_id: 'lead-1',
      status: 'contacted',
      first_contacted_at: '2026-07-22T12:12:30+00:00',
    };
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, changed: true, lead: serverLead }),
    });

    await expect(submitLeadLifecycleTransition(fetcher, 'lead-1', 'contacted'))
      .resolves.toEqual(serverLead);
    expect(fetcher).toHaveBeenCalledWith('/api/leads/lead-1/lifecycle', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'contacted' }),
    });
  });

  it('formats the measured server response interval', () => {
    expect(formatLeadResponseTime(
      '2026-07-22T12:00:00+00:00',
      '2026-07-22T12:12:30+00:00',
    )).toBe('12m');
    expect(formatLeadResponseTime(
      '2026-07-22T12:00:00+00:00',
      '2026-07-22T13:05:00+00:00',
    )).toBe('1h 5m');
  });
});
