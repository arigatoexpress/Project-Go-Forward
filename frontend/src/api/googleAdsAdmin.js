import adminFetch from '../adminFetch';

const STATUS_UNAVAILABLE = 'Paid Search status is unavailable.';
const STATES = ['INTERNAL_DRAFT', 'SERVER_VALIDATED'];

function isSafeEvent(event, index) {
  const expected = [
    ['00000000000000000001-internal-draft-created', 'INTERNAL_DRAFT_CREATED', 1, null, 'INTERNAL_DRAFT'],
    ['00000000000000000002-server-validated', 'SERVER_VALIDATED', 2, 'INTERNAL_DRAFT', 'SERVER_VALIDATED'],
  ][index];
  return event
    && expected
    && event.event_id === expected[0]
    && event.event_type === expected[1]
    && event.record_version === expected[2]
    && event.from_state === expected[3]
    && event.to_state === expected[4]
    && event.error_code === null
    && typeof event.occurred_at === 'string';
}

function normalizeStatus(payload) {
  const digest = payload?.contract_hash?.match(/^sha256:([a-f0-9]{64})$/)?.[1];
  const expectedWorkflow = payload?.state === 'INTERNAL_DRAFT'
    ? [['INTERNAL_DRAFT', 'current'], ['SERVER_VALIDATED', 'not_started']]
    : [['INTERNAL_DRAFT', 'complete'], ['SERVER_VALIDATED', 'current']];
  const items = payload?.events?.items;
  const expectedVersion = payload?.state === 'INTERNAL_DRAFT' ? 1 : 2;
  const safe = payload?.schema_version === 2
    && payload?.deployment_key === 'tho-search-high-intent-huffman-v1'
    && payload?.deployment_id === `${payload.deployment_key}--${digest}`
    && STATES.includes(payload?.state)
    && payload?.state_source === 'FIRESTORE_AUTHORITY_LEDGER'
    && payload?.version === expectedVersion
    && typeof payload?.updated_at === 'string'
    && payload?.connection?.state === 'NO_EVIDENCE'
    && payload?.connection?.verified_at === null
    && payload?.feature_enabled === false
    && payload?.ready === false
    && payload?.spend_enabled === false
    && payload?.budget?.average_daily_usd === 20
    && payload?.budget?.max_single_day_charge_usd === 40
    && payload?.budget?.monthly_charge_limit_usd === 608
    && payload?.budget?.max_cpc_usd === 5
    && Array.isArray(payload?.workflow)
    && payload.workflow.length === 2
    && payload.workflow.every((step, index) => (
      step?.state === expectedWorkflow[index][0] && step?.status === expectedWorkflow[index][1]
    ))
    && payload?.actions?.server_validation === (payload.state === 'INTERNAL_DRAFT')
    && Array.isArray(items)
    && payload?.events?.count === payload.version
    && items.length === payload.version
    && items.every(isSafeEvent);
  if (!safe) throw new Error(STATUS_UNAVAILABLE);

  return {
    schema_version: 2,
    deployment_id: payload.deployment_id,
    deployment_key: payload.deployment_key,
    contract_hash: payload.contract_hash,
    state: payload.state,
    state_source: 'FIRESTORE_AUTHORITY_LEDGER',
    version: payload.version,
    updated_at: payload.updated_at,
    connection: { state: 'NO_EVIDENCE', verified_at: null },
    feature_enabled: false,
    ready: false,
    spend_enabled: false,
    budget: { ...payload.budget },
    workflow: payload.workflow.map(step => ({ state: step.state, status: step.status })),
    actions: { server_validation: payload.actions.server_validation },
    events: {
      count: payload.events.count,
      items: items.map(event => ({
        event_id: event.event_id,
        event_type: event.event_type,
        record_version: event.record_version,
        from_state: event.from_state,
        to_state: event.to_state,
        error_code: null,
        occurred_at: event.occurred_at,
      })),
    },
  };
}

async function parseResponse(response) {
  if (!response.ok) throw new Error(STATUS_UNAVAILABLE);
  return normalizeStatus(await response.json());
}

export async function getGoogleAdsDeploymentReadiness() {
  try {
    return await parseResponse(await adminFetch('/api/admin/google-ads/deployment-readiness'));
  } catch (_error) {
    throw new Error(STATUS_UNAVAILABLE);
  }
}

export async function ensureGoogleAdsInternalDraft() {
  try {
    return await parseResponse(await adminFetch('/api/admin/google-ads/draft', {
      method: 'POST',
      retry: false,
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }));
  } catch (_error) {
    throw new Error(STATUS_UNAVAILABLE);
  }
}

export async function runGoogleAdsServerValidation(status, idempotencyKey) {
  try {
    const response = await adminFetch('/api/admin/google-ads/server-validation', {
      method: 'POST',
      retry: false,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        deployment_id: status.deployment_id,
        expected_version: status.version,
        idempotency_key: idempotencyKey,
      }),
    });
    return await parseResponse(response);
  } catch (_error) {
    throw new Error('Offline server validation failed. Retry when ready.');
  }
}
