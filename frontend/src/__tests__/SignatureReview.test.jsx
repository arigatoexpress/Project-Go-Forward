import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import adminFetch from '../adminFetch';
import SignatureReview from '../components/SignatureReview';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));
vi.mock('../downloadAdminFile', () => ({ default: vi.fn(() => Promise.resolve()) }));

const DEAL = { id: 'deal-1', buyer_email: 'alice@example.com' };

const SUMMARY = [
  { label: 'Sales price', value: '$80,000.00' },
  { label: 'Down payment', value: '$5,000.00' },
  { label: 'Total monthly payment', value: '$884.20' },
];

const READY_PREVIEW = {
  success: true,
  ready: true,
  esign_configured: true,
  document_label: 'Sales Contract',
  signer_email: 'alice@example.com',
  review_token: 'token-abc',
  download_url: '/api/documents/download/esign.pdf',
  filename: 'esign.pdf',
  problems: [],
  money_summary: SUMMARY,
};

function jsonResponse(body, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

function route(handlers) {
  adminFetch.mockImplementation((url, options = {}) => {
    const key = `${(options.method || 'GET').toUpperCase()} ${url}`;
    const handler = handlers[key];
    if (!handler) return jsonResponse({ success: true, requests: [] });
    return handler(options);
  });
}

function calls(method, url) {
  return adminFetch.mock.calls.filter(([u, o = {}]) => u === url && (o.method || 'GET').toUpperCase() === method);
}

describe('SignatureReview', () => {
  beforeEach(() => {
    adminFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows plain-English blockers and offers no send button when money lines are blank', async () => {
    route({
      'POST /api/deals/deal-1/esign/preview': () => jsonResponse({
        success: true,
        ready: false,
        esign_configured: true,
        document_label: 'Sales Contract',
        problems: [
          { field: 'down_payment', message: 'Down payment is blank or $0. Enter the down payment on the deal.' },
          { field: 'tax_rate', message: 'Property tax rate is blank or 0%. Enter the tax rate on the deal.' },
        ],
        money_summary: SUMMARY,
      }),
    });
    render(<SignatureReview deal={DEAL} />);

    fireEvent.click(screen.getByRole('button', { name: /review & send for signature/i }));

    expect(await screen.findByText(/can't send yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Down payment is blank or \$0/)).toBeInTheDocument();
    expect(screen.getByText(/Property tax rate is blank or 0%/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /send to/i })).toBeNull();
    expect(calls('POST', '/api/deals/deal-1/esign/send')).toHaveLength(0);
  });

  it('requires the hand-check box before sending, then sends the reviewed token', async () => {
    route({
      'POST /api/deals/deal-1/esign/preview': () => jsonResponse(READY_PREVIEW),
      'POST /api/deals/deal-1/esign/send': () => jsonResponse({
        success: true,
        message: 'Sales Contract sent to alice@example.com for signature.',
        submission_ids: ['42'],
      }),
    });
    render(<SignatureReview deal={DEAL} />);

    fireEvent.click(screen.getByRole('button', { name: /review & send for signature/i }));
    expect(await screen.findByText('$80,000.00')).toBeInTheDocument();
    expect(screen.getByText('$884.20')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open the filled sales contract/i })).toBeInTheDocument();

    const send = screen.getByRole('button', { name: /send to alice@example.com/i });
    expect(send).toBeDisabled();
    fireEvent.click(send);
    expect(calls('POST', '/api/deals/deal-1/esign/send')).toHaveLength(0);

    fireEvent.click(screen.getByRole('checkbox'));
    expect(send).not.toBeDisabled();
    fireEvent.click(send);

    expect(await screen.findByText('Sales Contract sent to alice@example.com for signature.')).toBeInTheDocument();
    const [, options] = calls('POST', '/api/deals/deal-1/esign/send')[0];
    expect(JSON.parse(options.body)).toEqual({
      template_name: 'TMHA_SalesContract.pdf',
      confirm: true,
      review_token: 'token-abc',
    });
  });

  it('shows the server refusal when the deal changed after review', async () => {
    route({
      'POST /api/deals/deal-1/esign/preview': () => jsonResponse(READY_PREVIEW),
      'POST /api/deals/deal-1/esign/send': () => jsonResponse({
        success: false,
        error: 'review_stale',
        message: 'Not sent. The deal changed after you reviewed it. Review the new preview before sending.',
      }, 409),
    });
    render(<SignatureReview deal={DEAL} />);

    fireEvent.click(screen.getByRole('button', { name: /review & send for signature/i }));
    fireEvent.click(await screen.findByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /send to alice@example.com/i }));

    expect(await screen.findByText(/deal changed after you reviewed it/i)).toBeInTheDocument();
  });

  it('disables sending when e-signing is not configured', async () => {
    route({
      'POST /api/deals/deal-1/esign/preview': () => jsonResponse({ ...READY_PREVIEW, esign_configured: false }),
    });
    render(<SignatureReview deal={DEAL} />);

    fireEvent.click(screen.getByRole('button', { name: /review & send for signature/i }));
    fireEvent.click(await screen.findByRole('checkbox'));
    expect(screen.getByText(/e-signing isn't turned on yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send to alice@example.com/i })).toBeDisabled();
  });

  it('cancels a pending signing request after confirmation', async () => {
    let cancelled = false;
    route({
      'GET /api/deals/deal-1/esign': () => jsonResponse({
        success: true,
        requests: cancelled
          ? [{ submission_id: '42', template_name: 'TMHA_SalesContract.pdf', status: 'cancelled' }]
          : [{ submission_id: '42', template_name: 'TMHA_SalesContract.pdf', status: 'pending', created_at: '2026-10-07T12:00:00Z' }],
      }),
      'POST /api/deals/deal-1/esign/42/cancel': () => {
        cancelled = true;
        return jsonResponse({ success: true, message: 'Signing request cancelled. The buyer can no longer sign it.' });
      },
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<SignatureReview deal={DEAL} />);

    const cancel = await screen.findByRole('button', { name: /cancel request/i });
    fireEvent.click(cancel);
    expect(calls('POST', '/api/deals/deal-1/esign/42/cancel')).toHaveLength(0);

    fireEvent.click(cancel);
    expect(await screen.findByText('Signing request cancelled. The buyer can no longer sign it.')).toBeInTheDocument();
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel request/i })).toBeNull());
  });
});
