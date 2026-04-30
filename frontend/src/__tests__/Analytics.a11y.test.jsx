import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Analytics from '../pages/Analytics';

// Analytics has no form inputs the way Contact / Appointments do — its a11y
// surface is the heading hierarchy, the <main> landmark, and the tab nav.
describe('Analytics a11y', () => {
  beforeEach(() => {
    // adminFetch wraps fetch; both end up calling globalThis.fetch.
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        total: 0,
        with_contact_info: 0,
        appointment_requested: 0,
        financing_discussed: 0,
        by_status: {},
        time_series: [],
        new_this_week: 0,
        trend: null,
        trend_up: false,
        status_trends: {},
      }),
    }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders an h1 page heading after data loads', async () => {
    render(<Analytics />);
    const heading = await waitFor(() =>
      screen.getByRole('heading', { level: 1, name: /analytics dashboard/i })
    );
    expect(heading).toBeInTheDocument();
  });

  it('wraps the page in a <main> landmark and exposes a tablist', async () => {
    render(<Analytics />);
    await waitFor(() => screen.getByRole('main'));
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('tablist', { name: /analytics sections/i })).toBeInTheDocument();
  });

  it('marks the active tab with aria-selected="true"', async () => {
    render(<Analytics />);
    const overviewTab = await waitFor(() =>
      screen.getByRole('tab', { name: /overview/i })
    );
    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
    // Other tabs default to false.
    expect(screen.getByRole('tab', { name: /leads/i })).toHaveAttribute('aria-selected', 'false');
  });
});
