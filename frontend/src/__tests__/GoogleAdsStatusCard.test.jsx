import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import adminFetch from '../adminFetch';
import GoogleAdsStatusCard from '../components/ad-studio/GoogleAdsStatusCard';
import {
  ensureGoogleAdsInternalDraft,
  getGoogleAdsDeploymentReadiness,
  getGoogleAdsPausedCreateApprovalReadiness,
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
    { state: 'PAUSED_CREATE_APPROVED', status: 'not_started' },
    { state: 'PAUSED_CREATED', status: 'not_started' },
  ],
  actions: { server_validation: true },
  paused_create: {
    outbox_state: null,
    activation_authorized: false,
    spend_enabled: false,
  },
  events: {
    count: 1,
    first_version: 1,
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
    { state: 'PAUSED_CREATE_APPROVED', status: 'not_started' },
    { state: 'PAUSED_CREATED', status: 'not_started' },
  ],
  actions: { server_validation: false },
  events: {
    count: 2,
    first_version: 1,
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

const APPROVAL_READY = {
  schema_version: 1,
  deployment_id: VALIDATED.deployment_id,
  contract_hash: VALIDATED.contract_hash,
  expected_version: 2,
  state: 'SERVER_VALIDATED',
  budget: { ...VALIDATED.budget },
  gates: {
    feature_enabled: true,
    cloud_readiness_verified: true,
    iam_verified: true,
    revision_bound: true,
    dispatcher_configured: true,
  },
  access_evidence_id: `sha256:${'b'.repeat(64)}`,
  access_evidence_fresh: true,
  action_available: true,
  dispatch_enabled: false,
  paused_only: true,
  activation_authorized: false,
  spend_enabled: false,
  remediation: [],
};

const APPROVED = {
  ...VALIDATED,
  state: 'PAUSED_CREATE_APPROVED',
  version: 3,
  workflow: [
    { state: 'INTERNAL_DRAFT', status: 'complete' },
    { state: 'SERVER_VALIDATED', status: 'complete' },
    { state: 'PAUSED_CREATE_APPROVED', status: 'current' },
    { state: 'PAUSED_CREATED', status: 'not_started' },
  ],
  paused_create: {
    outbox_state: 'PENDING',
    activation_authorized: false,
    spend_enabled: false,
  },
  events: {
    count: 3,
    first_version: 1,
    items: [
      ...VALIDATED.events.items,
      {
        event_id: '00000000000000000003-paused-create-approved',
        event_type: 'PAUSED_CREATE_APPROVED',
        record_version: 3,
        from_state: 'SERVER_VALIDATED',
        to_state: 'PAUSED_CREATE_APPROVED',
        error_code: null,
        occurred_at: '2026-08-12T12:02:00Z',
      },
    ],
  },
};

const CREATED = {
  ...APPROVED,
  state: 'PAUSED_CREATED',
  version: 4,
  workflow: [
    { state: 'INTERNAL_DRAFT', status: 'complete' },
    { state: 'SERVER_VALIDATED', status: 'complete' },
    { state: 'PAUSED_CREATE_APPROVED', status: 'complete' },
    { state: 'PAUSED_CREATED', status: 'current' },
  ],
  paused_create: {
    outbox_state: 'DISPATCHED',
    activation_authorized: false,
    spend_enabled: false,
  },
  events: {
    count: 4,
    first_version: 1,
    items: [
      ...APPROVED.events.items,
      {
        event_id: '00000000000000000004-paused-create-completed',
        event_type: 'PAUSED_CREATE_COMPLETED',
        record_version: 4,
        from_state: 'PAUSED_CREATE_APPROVED',
        to_state: 'PAUSED_CREATED',
        error_code: null,
        occurred_at: '2026-08-12T12:03:00Z',
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

  it('accepts a contiguous sanitized 100-event tail for a longer durable history', async () => {
    const tail = Array.from({ length: 100 }, (_, index) => {
      const recordVersion = index + 4;
      const claimed = recordVersion % 2 === 0;
      const eventType = claimed ? 'PAUSED_CREATE_CLAIMED' : 'PAUSED_CREATE_CLAIM_RELEASED';
      return {
        event_id: `${String(recordVersion).padStart(20, '0')}-${eventType.toLowerCase().replaceAll('_', '-')}`,
        event_type: eventType,
        record_version: recordVersion,
        from_state: 'PAUSED_CREATE_APPROVED',
        to_state: 'PAUSED_CREATE_APPROVED',
        error_code: claimed ? null : 'provider_validation_failed',
        occurred_at: '2026-08-12T12:03:00Z',
      };
    });
    const longStatus = {
      ...APPROVED,
      version: 103,
      events: { count: 103, first_version: 4, items: tail },
    };
    adminFetch.mockResolvedValue(response(longStatus));

    await expect(getGoogleAdsDeploymentReadiness()).resolves.toEqual(longStatus);
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

  it('accepts only exact fail-closed PAUSED approval readiness', async () => {
    adminFetch.mockResolvedValue(response(APPROVAL_READY));
    await expect(getGoogleAdsPausedCreateApprovalReadiness()).resolves.toEqual(APPROVAL_READY);
    adminFetch.mockResolvedValue(response({ ...APPROVAL_READY, spend_enabled: true }));
    await expect(getGoogleAdsPausedCreateApprovalReadiness()).rejects.toThrow(/owner passkey/i);
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
    expect(screen.getByRole('heading', { name: 'Owner PAUSED-create approval' })).toBeInTheDocument();
    expect(screen.getByText('Complete offline server validation first.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verify owner and approve PAUSED creation' })).toBeDisabled();
    expect(screen.getByText(/PAUSED only · \$0 spend/i)).toBeInTheDocument();
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

  it('requires confirmation and sends only proof IDs to PAUSED approval', async () => {
    const proofId = `sha256:${'c'.repeat(64)}`;
    const accessId = APPROVAL_READY.access_evidence_id;
    adminFetch
      .mockResolvedValueOnce(response(VALIDATED))
      .mockResolvedValueOnce(response(APPROVAL_READY))
      .mockResolvedValueOnce(response({
        challenge: 'AQ',
        allowCredentials: [{ id: 'Ag', type: 'public-key' }],
      }))
      .mockResolvedValueOnce(response({
        verified: true,
        proof_reference: 'signed-proof-reference-that-is-long-enough',
        proof_id: proofId,
        access_evidence_id: accessId,
      }))
      .mockResolvedValueOnce(response({
        deployment_id: VALIDATED.deployment_id,
        contract_hash: VALIDATED.contract_hash,
        state: 'PAUSED_CREATE_APPROVED',
        version: 3,
        outbox_state: 'PENDING',
        replayed: false,
        paused_only: true,
        activation_authorized: false,
        spend_enabled: false,
      }))
      .mockResolvedValueOnce(response(APPROVED));
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    Object.defineProperty(globalThis.navigator, 'credentials', {
      configurable: true,
      value: {
        get: vi.fn().mockResolvedValue({
          id: 'owner-key',
          rawId: new Uint8Array([1]).buffer,
          type: 'public-key',
          response: {
            authenticatorData: new Uint8Array([2]).buffer,
            clientDataJSON: new Uint8Array([3]).buffer,
            signature: new Uint8Array([4]).buffer,
            userHandle: null,
          },
        }),
      },
    });

    render(<GoogleAdsStatusCard />);
    const button = await screen.findByRole('button', {
      name: 'Verify owner and approve PAUSED creation',
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    expect(await screen.findByText(
      /PAUSED-only creation is approved; durable outbox is pending/i,
    )).toBeInTheDocument();
    expect(screen.getAllByText('PAUSED creation approved')).toHaveLength(2);
    expect(screen.getByText('3 recorded events')).toBeInTheDocument();
    const approvalCall = adminFetch.mock.calls.find(([url]) => (
      url === '/api/admin/google-ads/paused-create-approval'
    ));
    expect(JSON.parse(approvalCall[1].body)).toEqual({
      deployment_id: VALIDATED.deployment_id,
      expected_version: 2,
      proof_reference: 'signed-proof-reference-that-is-long-enough',
      proof_id: proofId,
      access_evidence_id: accessId,
    });
    expect(approvalCall[1].body).not.toMatch(/caps|account|provider|activate|spend/i);
  });

  it('shows dispatch state and blocks owner approval when dispatch is already enabled', async () => {
    const unsafeReadiness = {
      ...APPROVAL_READY,
      action_available: false,
      dispatch_enabled: true,
      remediation: ['Disable PAUSED-create dispatch before owner approval.'],
    };
    adminFetch
      .mockResolvedValueOnce(response(VALIDATED))
      .mockResolvedValueOnce(response(unsafeReadiness));

    render(<GoogleAdsStatusCard />);

    expect(await screen.findByText(/Dispatch state/i)).toBeInTheDocument();
    expect(screen.getByText(/Enabled — approval locked/i)).toBeInTheDocument();
    expect(screen.getByText(/Disable PAUSED-create dispatch before owner approval/i)).toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Verify owner and approve PAUSED creation',
    })).toBeDisabled();
    expect(adminFetch).toHaveBeenCalledTimes(2);
  });

  it('reports a durable approved outbox truthfully and keeps approval disabled', async () => {
    adminFetch.mockResolvedValueOnce(response(APPROVED));

    render(<GoogleAdsStatusCard />);

    expect(await screen.findByText(
      /PAUSED-only creation is approved; durable outbox is pending/i,
    )).toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Verify owner and approve PAUSED creation',
    })).toBeDisabled();
    expect(screen.queryByText('Complete offline server validation first.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /activate|publish|spend/i })).not.toBeInTheDocument();
    expect(adminFetch).toHaveBeenCalledTimes(1);
  });

  it('never reports approval failure when only the post-approval refresh is unavailable', async () => {
    const proofId = `sha256:${'c'.repeat(64)}`;
    adminFetch
      .mockResolvedValueOnce(response(VALIDATED))
      .mockResolvedValueOnce(response(APPROVAL_READY))
      .mockResolvedValueOnce(response({
        challenge: 'AQ',
        allowCredentials: [{ id: 'Ag', type: 'public-key' }],
      }))
      .mockResolvedValueOnce(response({
        verified: true,
        proof_reference: 'signed-proof-reference-that-is-long-enough',
        proof_id: proofId,
        access_evidence_id: APPROVAL_READY.access_evidence_id,
      }))
      .mockResolvedValueOnce(response({
        deployment_id: VALIDATED.deployment_id,
        contract_hash: VALIDATED.contract_hash,
        state: 'PAUSED_CREATE_APPROVED',
        version: 3,
        outbox_state: 'PENDING',
        replayed: false,
        paused_only: true,
        activation_authorized: false,
        spend_enabled: false,
      }))
      .mockResolvedValueOnce(response({}, 503));
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    Object.defineProperty(globalThis.navigator, 'credentials', {
      configurable: true,
      value: {
        get: vi.fn().mockResolvedValue({
          id: 'owner-key',
          rawId: new Uint8Array([1]).buffer,
          type: 'public-key',
          response: {
            authenticatorData: new Uint8Array([2]).buffer,
            clientDataJSON: new Uint8Array([3]).buffer,
            signature: new Uint8Array([4]).buffer,
            userHandle: null,
          },
        }),
      },
    });

    render(<GoogleAdsStatusCard />);
    const button = await screen.findByRole('button', {
      name: 'Verify owner and approve PAUSED creation',
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /approval was recorded, but durable status refresh is unavailable/i,
    );
    expect(screen.getByText(/PAUSED-only approval recorded.*no spend is enabled/i)).toBeInTheDocument();
    expect(screen.queryByText(/PAUSED-only approval failed/i)).not.toBeInTheDocument();
  });

  it('shows bounded-dispatch failure remediation without enabling another control', async () => {
    const failedStatus = {
      ...APPROVED,
      paused_create: { ...APPROVED.paused_create, outbox_state: 'FAILED' },
    };
    adminFetch.mockResolvedValueOnce(response(failedStatus));

    render(<GoogleAdsStatusCard />);

    expect(await screen.findByText(/bounded dispatcher attempts are exhausted/i)).toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Verify owner and approve PAUSED creation',
    })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /retry|activate|publish|spend/i })).not.toBeInTheDocument();
  });

  it('reports reconciled PAUSED resources without exposing a second approval', async () => {
    adminFetch.mockResolvedValueOnce(response(CREATED));

    render(<GoogleAdsStatusCard />);

    expect(await screen.findByText(
      /PAUSED Google Ads resources were created and reconciled.*remain inactive/i,
    )).toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Verify owner and approve PAUSED creation',
    })).toBeDisabled();
    expect(screen.queryByText('Complete offline server validation first.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /activate|publish|spend/i })).not.toBeInTheDocument();
    expect(adminFetch).toHaveBeenCalledTimes(1);
  });
});
