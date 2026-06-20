import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ReportIssue from '../components/ReportIssue';

// Regression guard: the report widget used to ignore resp.ok and always show a
// success checkmark — so a failed POST (4xx/5xx) silently dropped the report.

describe('ReportIssue', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function openAndSend(text) {
    fireEvent.click(screen.getByTitle('Report an issue'));
    fireEvent.change(screen.getByPlaceholderText(/Describe what happened/i), {
      target: { value: text },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send Report/i }));
  }

  it('shows a failure message (NOT success) when the backend POST fails', async () => {
    global.fetch.mockResolvedValue({ ok: false });
    render(<ReportIssue />);
    openAndSend('Inventory page is blank');

    expect(await screen.findByText(/Couldn't send your report/i)).toBeInTheDocument();
    expect(screen.queryByText(/We'll look into it/i)).not.toBeInTheDocument();
  });

  it('shows success when the report actually sends', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    render(<ReportIssue />);
    openAndSend('Just a note');

    expect(await screen.findByText(/We'll look into it/i)).toBeInTheDocument();
  });
});
