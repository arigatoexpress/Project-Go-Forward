import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import CRM from '../pages/CRM';
import ReviewRequestCard from '../components/ReviewRequestCard';
import {
  SALESPERSON_STORAGE_KEY,
  buildMailtoHref,
  buildReviewMessage,
  buildSmsHref,
  firstNameOf,
} from '../reviewRequest';

const REVIEW_LINK = 'https://g.page/r/TESTID/review';
const SAFETY_VALVE_PHONE = '(281) 324-3020';

// ─── Pure helpers ───

describe('review message template', () => {
  it('greets by first name and includes salesperson, link, and safety valve', () => {
    const msg = buildReviewMessage({
      customerName: 'Maria Lopez',
      salesperson: 'Celeste',
      reviewLink: REVIEW_LINK,
    });
    expect(msg).toContain('Hi Maria!');
    expect(msg).toContain('Celeste at Texas Home Outlet');
    expect(msg).toContain(REVIEW_LINK);
    // Safety valve: unhappy customers call us BEFORE leaving a Google review.
    expect(msg).toContain(`call me first at ${SAFETY_VALVE_PHONE}`);
  });

  it('falls back gracefully when names are missing', () => {
    const msg = buildReviewMessage({ customerName: '', salesperson: '', reviewLink: REVIEW_LINK });
    expect(msg).toContain('Hi there!');
    expect(msg).toContain('This is Texas Home Outlet.');
    expect(firstNameOf('  Bob   Jones ')).toBe('Bob');
  });
});

describe('sms and mailto href builders', () => {
  it('builds an sms: href with digits-only number and URL-encoded body', () => {
    const href = buildSmsHref('(281) 555-0123', 'Review us: https://g.page/r/X?a=b c');
    expect(href.startsWith('sms:2815550123?&body=')).toBe(true);
    expect(href).toContain(encodeURIComponent('https://g.page/r/X?a=b c'));
    expect(href).not.toContain('b c'); // spaces must be encoded
  });

  it('preserves a leading + in the phone number', () => {
    expect(buildSmsHref('+1 (281) 555-0123', 'hi')).toBe('sms:+12815550123?&body=hi');
  });

  it('builds a mailto: href with encoded subject and body', () => {
    const href = buildMailtoHref('maria@example.com', 'How was it?', 'Line one\nLink: https://g.page/r/X');
    expect(href.startsWith('mailto:maria%40example.com?subject=How%20was%20it%3F&body=')).toBe(true);
    expect(href).toContain(encodeURIComponent('Line one\nLink: https://g.page/r/X'));
  });
});

// ─── Component ───

describe('ReviewRequestCard', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('renders nothing when no review link is configured', () => {
    const { container } = render(<ReviewRequestCard reviewLink="" />);
    expect(container.firstChild).toBeNull();
  });

  it('prefills from the lead and exposes correct sms/mailto links', () => {
    render(
      <ReviewRequestCard
        reviewLink={REVIEW_LINK}
        lead={{ name: 'Maria Lopez', phone: '(281) 555-0123', email: 'maria@example.com' }}
      />,
    );
    const expectedMsg = buildReviewMessage({
      customerName: 'Maria Lopez',
      salesperson: '',
      reviewLink: REVIEW_LINK,
    });
    expect(screen.getByLabelText(/Message preview/i).value).toBe(expectedMsg);

    const smsLink = screen.getByRole('link', { name: /Text customer/i });
    expect(smsLink.getAttribute('href')).toBe(buildSmsHref('(281) 555-0123', expectedMsg));

    const mailLink = screen.getByRole('link', { name: /Email customer/i });
    expect(mailLink.getAttribute('href')).toBe(
      buildMailtoHref('maria@example.com', 'How was your experience with Texas Home Outlet?', expectedMsg),
    );
  });

  it('remembers the salesperson name in localStorage and injects it into the message', () => {
    render(<ReviewRequestCard reviewLink={REVIEW_LINK} lead={{ name: 'Maria Lopez' }} />);
    const nameInput = screen.getByLabelText(/Your name/i);
    fireEvent.change(nameInput, { target: { value: 'Celeste' } });

    expect(localStorage.getItem(SALESPERSON_STORAGE_KEY)).toBe('Celeste');
    const msg = screen.getByLabelText(/Message preview/i).value;
    expect(msg).toContain('Celeste at Texas Home Outlet');
  });
});

// ─── CRM integration: feature hidden until the link is configured ───

function mockCrmFetch({ reviewsEnabled }) {
  return vi.fn((url) => {
    const u = typeof url === 'string' ? url : '';
    if (u.includes('/api/admin/reviews/config')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => (reviewsEnabled
          ? { success: true, enabled: true, review_link: REVIEW_LINK }
          : { success: true, enabled: false, review_link: null }),
      });
    }
    if (u.includes('/api/leads')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          leads: [{ lead_id: 'l1', name: 'Maria Lopez', phone: '(281) 555-0123', email: 'maria@example.com', status: 'converted', created_at: '2026-06-01T00:00:00Z' }],
        }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: true }) });
  });
}

describe('CRM review-request integration', () => {
  beforeEach(() => {
    // adminFetch reads document.cookie for CSRF; jsdom provides it.
    localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('hides the Reviews tab and row action when the link is unset', async () => {
    global.fetch = mockCrmFetch({ reviewsEnabled: false });
    render(<CRM onBack={() => {}} />);

    await waitFor(() => expect(screen.getByText('Maria Lopez')).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Reviews/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Request review/i })).toBeNull();
  });

  it('shows the Reviews tab and per-lead action when the link is configured', async () => {
    global.fetch = mockCrmFetch({ reviewsEnabled: true });
    render(<CRM onBack={() => {}} />);

    await waitFor(() => expect(screen.getByText('Maria Lopez')).toBeTruthy());
    await waitFor(() => expect(screen.getByRole('button', { name: /^Reviews$/i })).toBeTruthy());

    // Open the per-lead review modal and check the prefilled message.
    fireEvent.click(screen.getByRole('button', { name: /Request review from Maria Lopez/i }));
    const expectedMsg = buildReviewMessage({
      customerName: 'Maria Lopez',
      salesperson: '',
      reviewLink: REVIEW_LINK,
    });
    expect(screen.getByLabelText(/Message preview/i).value).toBe(expectedMsg);
    expect(screen.getByRole('link', { name: /Text customer/i }).getAttribute('href'))
      .toBe(buildSmsHref('(281) 555-0123', expectedMsg));
  });
});
