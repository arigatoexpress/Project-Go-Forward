import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import adminFetch from '../adminFetch';
import GoogleAdsStatusCard from '../components/ad-studio/GoogleAdsStatusCard';
import {
  ensureGoogleAdsInternalDraft,
  getGoogleAdsDeploymentReadiness,
  runGoogleAdsServerValidation,
} from '../api/googleAdsAdmin';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));

const SAFE_STATUS = {
  schema_version: 2,
  deployment_id: `tho-search-high-intent-huffman-v1--${'a'.repeat(64)}`,
  deployment_key: 'tho-search-high-intent-huffman-v1',
  contract_hash: `sha256:${'a'.repeat(64)}`,
  state: 'INTERNAL_DRAFT',
  state_source: 'FIRESTORE_AUTHORITY_LEDGER',
  version: 1,
  updated_at: '2026-08-12T12:00:00Z',
  connection: { state: 'NO_EVIDENCE', verified_at: null },
  feature_enabled: false,
  ready: false,
  spend_enabled: false,
  budget: {
    average_daily_usd: 20,
    max_single_day_charge_usd: 40,
    monthly_charge_limit_usd: 608,
    max_cpc_usd: 5,
  },
  workflow: [
    { state: 'INTERNAL_DRAFT', status: 'current' },
    { state: 'SERVER_VALIDATED', status: 'not_started' },
  ],
  actions: { server_validation: true },
  events: {
    count: 1,
    items: [{
      event_id: '00000000000000000001-internal-draft-created',
      event_type: 'INTERNAL_DRAFT_CREATED',
      record_version: 1,
      from_state: null,
      to_state: 'INTERNAL_DRAFT',
      error_code: null,
      occurred_at: '2026-08-12T12:00:00Z',
    }],
  },
};

const VALIDATED = {
  ...SAFE_STATUS,
  state: 'SERVER_VALIDATED',
  version: 2,
  workflow: [
    { state: 'INTERNAL_DRAFT', status: 'complete' },
    { state: 'SERVER_VALIDATED', status: 'current' },
  ],
  actions: { server_validation: false },
  events: {
    count: 2,
    items: [
      ...SAFE_STATUS.events.items,
      {
        event_id: '00000000000000000002-server-validated',
        event_type: 'SERVER_VALIDATED',
        record_version: 2,
        from_state: 'INTERNAL_DRAFT',
        to_state: 'SERVER_VALIDATED',
        error_code: null,
        occurred_at: '2026-08-12T12:01:00Z',
      },
    ],
  },
};

function response(payload = SAFE_STATUS, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: vi.fn().mockResolvedValue(payload) };
}

describe('Paid Search durable API', () => {
  beforeEach(() => adminFetch.mockReset());

  it('loads the authenticated durable readiness projection', async () => {
    adminFetch.mockResolvedValue(response());
    await expect(getGoogleAdsDeploymentReadiness()).resolves.toEqual(SAFE_STATUS);
    expect(adminFetch).toHaveBeenCalledWith('/api/admin/google-ads/deployment-readiness');
  });

  it('bootstraps only through a non-retrying CSRF-aware POST', async () => {
    adminFetch.mockResolvedValue(response());
    await expect(ensureGoogleAdsInternalDraft()).resolves.toEqual(SAFE_STATUS);
    expect(adminFetch).toHaveBeenCalledWith('/api/admin/google-ads/draft', {
      method: 'POST',
      retry: false,
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
  });

  it('posts only identity, version, and an idempotency key with retries disabled', async () => {
    adminFetch.mockResolvedValue(response(VALIDATED));
    await expect(runGoogleAdsServerValidation(
      SAFE_STATUS,
      'offline-validation-00000000-0000-4000-8000-000000000000',
    )).resolves.toEqual(VALIDATED);

    const [url, options] = adminFetch.mock.calls[0];
    expect(url).toBe('/api/admin/google-ads/server-validation');
    expect(options.method).toBe('POST');
    expect(options.retry).toBe(false);
    expect(options.headers).toEqual({ 'Content-Type': 'application/json' });
    const body = JSON.parse(options.body);
    expect(body).toEqual({
      deployment_id: SAFE_STATUS.deployment_id,
      expected_version: 1,
      idempotency_key: 'offline-validation-00000000-0000-4000-8000-000000000000',
    });
  });

  it('rejects unknown fields or relaxed authority in responses', async () => {
    adminFetch.mockResolvedValue(response({ ...SAFE_STATUS, spend_enabled: true }));
    await expect(getGoogleAdsDeploymentReadiness()).rejects.toThrow('Paid Search status is unavailable.');
  });

  it.each([
    { ...SAFE_STATUS, version: 999 },
    { ...SAFE_STATUS, state: 'SERVER_VALIDATED' },
    { ...SAFE_STATUS, events: { count: 999, items: SAFE_STATUS.events.items } },
    {
      ...SAFE_STATUS,
      events: {
        count: 1,
        items: [{ ...SAFE_STATUS.events.items[0], event_id: '00000000000000000999-internal-draft-created' }],
      },
    },
  ])('rejects impossible state/version/event evidence', async (unsafe) => {
    adminFetch.mockResolvedValue(response(unsafe));
    await expect(getGoogleAdsDeploymentReadiness()).rejects.toThrow('Paid Search status is unavailable.');
  });
});

describe('GoogleAdsStatusCard', () => {
  beforeEach(() => adminFetch.mockReset());

  it('shows durable state and the single offline validation action', async () => {
    adminFetch.mockResolvedValue(response());
    render(<GoogleAdsStatusCard />);

    expect(await screen.findByRole('heading', { name: 'Paid Search' })).toBeInTheDocument();
    expect(screen.getByText('Durable review state')).toBeInTheDocument();
    expect(screen.getByText('1 recorded event')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run offline server validation' })).toBeEnabled();
    expect(screen.queryByText(/approve/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/create campaign/i)).not.toBeInTheDocument();
    expect(adminFetch).toHaveBeenCalledWith('/api/admin/google-ads/draft', {
      method: 'POST',
      retry: false,
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
  });

  it('submits once, disables while pending, and renders the durable result', async () => {
    let resolvePost;
    adminFetch
      .mockResolvedValueOnce(response())
      .mockReturnValueOnce(new Promise(resolve => { resolvePost = resolve; }));
    render(<GoogleAdsStatusCard />);
    const button = await screen.findByRole('button', { name: 'Run offline server validation' });

    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('Validating offline…');
    fireEvent.click(button);
    expect(adminFetch).toHaveBeenCalledTimes(2);

    resolvePost(response(VALIDATED));
    await waitFor(() => expect(screen.getByText('Offline server validation complete.')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Run offline server validation' })).toBeDisabled();
    expect(screen.getByText('2 recorded events')).toBeInTheDocument();
  });

  it('surfaces a safe retryable failure without automatic POST retry', async () => {
    adminFetch
      .mockResolvedValueOnce(response())
      .mockResolvedValueOnce(response({ message: 'safe' }, 503));
    render(<GoogleAdsStatusCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run offline server validation' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Offline server validation failed. Retry when ready.');
    expect(adminFetch).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Run offline server validation' })).toBeEnabled();

    adminFetch.mockResolvedValueOnce(response(VALIDATED));
    fireEvent.click(screen.getByRole('button', { name: 'Run offline server validation' }));
    await screen.findByText('Offline server validation complete.');
    const firstKey = JSON.parse(adminFetch.mock.calls[1][1].body).idempotency_key;
    const retryKey = JSON.parse(adminFetch.mock.calls[2][1].body).idempotency_key;
    expect(retryKey).toBe(firstKey);
  });
});
