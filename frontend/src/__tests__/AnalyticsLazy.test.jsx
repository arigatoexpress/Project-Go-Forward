import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import Analytics from '../pages/Analytics';
import adminFetch from '../adminFetch';

vi.mock('../adminFetch', () => ({ default: vi.fn(() => new Promise(() => {})) }));

describe('Analytics lazy load', () => {
  it('shows a skeleton instead of blocking on Loading analytics', () => {
    render(<Analytics />);
    expect(screen.getByRole('heading', { name: 'Numbers' })).toBeInTheDocument();
    expect(screen.getByTestId('analytics-skeleton')).toBeInTheDocument();
    expect(screen.queryByText('Loading analytics...')).toBeNull();
    expect(adminFetch).toHaveBeenCalled();
  });

  it('explains a load failure without taking over the whole app', async () => {
    adminFetch.mockResolvedValue({ ok: false, json: async () => ({}) });
    render(<Analytics />);
    expect(await screen.findByText(/These numbers could not load/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
