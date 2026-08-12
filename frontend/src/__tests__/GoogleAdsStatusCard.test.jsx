import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import adminFetch from '../adminFetch';
import GoogleAdsStatusCard from '../components/ad-studio/GoogleAdsStatusCard';
import { getGoogleAdsDeploymentReadiness } from '../api/googleAdsAdmin';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

const SAFE_STATUS = {
  schema_version: 1,
  deployment_id: `tho-search-high-intent-huffman-v1--${'a'.repeat(64)}`,
  deployment_key: 'tho-search-high-intent-huffman-v1',
  contract_hash: `sha256:${'a'.repeat(64)}`,
  state: 'INTERNAL_DRAFT',
  state_source: 'CHECKED_IN_CONTRACT',
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
    { state: 'PAUSED_CREATE_APPROVED', status: 'locked' },
    { state: 'PAUSED_CREATED', status: 'locked' },
  ],
  actions: {
    review: false,
    approve_paused_create: false,
    create_paused: false,
    activate: false,
  },
};

function okResponse(payload = SAFE_STATUS) {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(payload),
  };
}

describe('Paid Search status API', () => {
  beforeEach(() => {
    adminFetch.mockReset();
  });

  it('uses the authenticated GET-only readiness endpoint', async () => {
    adminFetch.mockResolvedValue(okResponse());

    await expect(getGoogleAdsDeploymentReadiness()).resolves.toEqual(SAFE_STATUS);

    expect(adminFetch).toHaveBeenCalledTimes(1);
    expect(adminFetch).toHaveBeenCalledWith('/api/admin/google-ads/deployment-readiness');
  });

  it('rejects a response that relaxes any spend boundary', async () => {
    adminFetch.mockResolvedValue(okResponse({
      ...SAFE_STATUS,
      actions: { ...SAFE_STATUS.actions, activate: true },
    }));

    await expect(getGoogleAdsDeploymentReadiness()).rejects.toThrow(
      'Paid Search status is unavailable.',
    );
  });
});

describe('GoogleAdsStatusCard', () => {
  beforeEach(() => {
    adminFetch.mockReset();
  });

  it('renders NO_EVIDENCE, exact reviewed caps, and the inert state ladder', async () => {
    adminFetch.mockResolvedValue(okResponse());

    render(<GoogleAdsStatusCard />);

    expect(await screen.findByRole('heading', { name: 'Paid Search' })).toBeInTheDocument();
    expect(screen.getByText('Account access not verified')).toBeInTheDocument();
    expect(screen.getByText(
      '$20 average daily budget, up to $40 in a single day, $608 monthly charging limit, and $5 maximum CPC.',
    )).toBeInTheDocument();
    expect(screen.getByText('Internal draft')).toBeInTheDocument();
    expect(screen.getByText('Server validated')).toBeInTheDocument();
    expect(screen.getByText('Paused create approved')).toBeInTheDocument();
    expect(screen.getByText('Paused created')).toBeInTheDocument();
    expect(screen.getByText('All campaign and spend actions are locked.')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('never renders provider-shaped or unexpected response fields', async () => {
    adminFetch.mockResolvedValue(okResponse({
      ...SAFE_STATUS,
      customer_id: '123-456-7890',
      developer_token: 'raw-token',
      provider_error: 'customers/123/campaigns/456',
    }));

    render(<GoogleAdsStatusCard />);
    await screen.findByText('Account access not verified');

    expect(screen.queryByText('123-456-7890')).not.toBeInTheDocument();
    expect(screen.queryByText('raw-token')).not.toBeInTheDocument();
    expect(screen.queryByText('customers/123/campaigns/456')).not.toBeInTheDocument();
  });

  it('announces a generic failure without leaking the raw error', async () => {
    adminFetch.mockRejectedValue(new Error('customers/123 raw-provider-secret'));

    render(<GoogleAdsStatusCard />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Paid Search status is unavailable.');
    expect(alert).not.toHaveTextContent('customers/123');
    expect(alert).not.toHaveTextContent('raw-provider-secret');
  });

  it('exposes a polite loading announcement', async () => {
    adminFetch.mockReturnValue(new Promise(() => {}));

    render(<GoogleAdsStatusCard />);

    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('Loading Paid Search status…');
    await waitFor(() => expect(adminFetch).toHaveBeenCalledTimes(1));
  });
});
