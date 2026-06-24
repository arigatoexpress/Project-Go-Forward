import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import ClosureBanner from '../components/ClosureBanner';

// Fixed closure window used across the deterministic cases below. We always
// inject an explicit `now` so the test never depends on the real clock and
// cannot become a time-bomb as the real date passes the closure window.
const CLOSURE = {
  start: '2026-07-02',
  end: '2026-07-07',
  reopen: '2026-07-08',
  message: 'Closed July 2–7 for the Independence Day holiday. We reopen July 8.',
};

const STORAGE_KEY = 'tho-closure-dismissed';

beforeEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe('ClosureBanner', () => {
  it('renders the closure message during the closure window', () => {
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-07-03T12:00:00')} />);
    const banner = screen.getByRole('status');
    expect(banner).toBeInTheDocument();
    expect(screen.getByText(CLOSURE.message)).toBeInTheDocument();
  });

  it('renders on the final closed day (end date is inclusive)', () => {
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-07-07T23:00:00')} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('renders before the closure starts (gives visitors advance notice)', () => {
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-06-24T09:00:00')} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('is hidden after the end date (auto-hides once reopened)', () => {
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-07-08T00:00:00')} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('is hidden well after the closure window', () => {
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-12-25T00:00:00')} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('is hidden when the closure config is null (banner disabled)', () => {
    render(<ClosureBanner closure={null} now={new Date('2026-07-03T12:00:00')} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('is hidden when previously dismissed in this browser', () => {
    window.localStorage.setItem(STORAGE_KEY, CLOSURE.end);
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-07-03T12:00:00')} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('dismisses and persists when the close button is clicked', () => {
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-07-03T12:00:00')} />);
    const closeBtn = screen.getByLabelText(/dismiss closure notice/i);
    act(() => {
      closeBtn.click();
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(CLOSURE.end);
  });

  it('re-shows for a NEW closure window even if an older one was dismissed', () => {
    // Stored dismissal is keyed to a prior end date; a new window should ignore it.
    window.localStorage.setItem(STORAGE_KEY, '2026-01-01');
    render(<ClosureBanner closure={CLOSURE} now={new Date('2026-07-03T12:00:00')} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
