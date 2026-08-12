import adminFetch from '../adminFetch';

const STATUS_UNAVAILABLE = 'Paid Search status is unavailable.';
const EXPECTED_WORKFLOW = [
  ['INTERNAL_DRAFT', 'current'],
  ['SERVER_VALIDATED', 'not_started'],
  ['PAUSED_CREATE_APPROVED', 'locked'],
  ['PAUSED_CREATED', 'locked'],
];

function isExactBudget(budget) {
  return budget?.average_daily_usd === 20
    && budget?.max_single_day_charge_usd === 40
    && budget?.monthly_charge_limit_usd === 608
    && budget?.max_cpc_usd === 5;
}

function hasExactWorkflow(workflow) {
  return Array.isArray(workflow)
    && workflow.length === EXPECTED_WORKFLOW.length
    && workflow.every((item, index) => (
      item?.state === EXPECTED_WORKFLOW[index][0]
      && item?.status === EXPECTED_WORKFLOW[index][1]
    ));
}

function normalizeStatus(payload) {
  const digest = typeof payload?.contract_hash === 'string'
    ? payload.contract_hash.match(/^sha256:([a-f0-9]{64})$/)?.[1]
    : undefined;
  const validDeployment = payload?.deployment_key === 'tho-search-high-intent-huffman-v1'
    && payload?.deployment_id === `${payload.deployment_key}--${digest}`;
  const actionsLocked = payload?.actions?.review === false
    && payload?.actions?.approve_paused_create === false
    && payload?.actions?.create_paused === false
    && payload?.actions?.activate === false;
  const safe = payload?.schema_version === 1
    && validDeployment
    && payload?.state === 'INTERNAL_DRAFT'
    && payload?.state_source === 'CHECKED_IN_CONTRACT'
    && payload?.connection?.state === 'NO_EVIDENCE'
    && payload?.connection?.verified_at === null
    && payload?.feature_enabled === false
    && payload?.ready === false
    && payload?.spend_enabled === false
    && isExactBudget(payload?.budget)
    && hasExactWorkflow(payload?.workflow)
    && actionsLocked;

  if (!safe) throw new Error(STATUS_UNAVAILABLE);

  return {
    schema_version: 1,
    deployment_id: payload.deployment_id,
    deployment_key: payload.deployment_key,
    contract_hash: payload.contract_hash,
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
    workflow: EXPECTED_WORKFLOW.map(([state, status]) => ({ state, status })),
    actions: {
      review: false,
      approve_paused_create: false,
      create_paused: false,
      activate: false,
    },
  };
}

export async function getGoogleAdsDeploymentReadiness() {
  try {
    const response = await adminFetch('/api/admin/google-ads/deployment-readiness');
    if (!response.ok) throw new Error(STATUS_UNAVAILABLE);
    return normalizeStatus(await response.json());
  } catch (_error) {
    throw new Error(STATUS_UNAVAILABLE);
  }
}
