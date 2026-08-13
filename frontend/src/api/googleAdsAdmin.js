import adminFetch from '../adminFetch';

const STATUS_UNAVAILABLE = 'Paid Search status is unavailable.';
const STATES = ['INTERNAL_DRAFT', 'SERVER_VALIDATED', 'PAUSED_CREATE_APPROVED', 'PAUSED_CREATED'];

function isSafeEvent(event, index, firstVersion) {
  const recordVersion = firstVersion + index;
  const initial = [
    ['00000000000000000001-internal-draft-created', 'INTERNAL_DRAFT_CREATED', 1, null, 'INTERNAL_DRAFT'],
    ['00000000000000000002-server-validated', 'SERVER_VALIDATED', 2, 'INTERNAL_DRAFT', 'SERVER_VALIDATED'],
    ['00000000000000000003-paused-create-approved', 'PAUSED_CREATE_APPROVED', 3, 'SERVER_VALIDATED', 'PAUSED_CREATE_APPROVED'],
  ][recordVersion - 1];
  const expected = initial || [
    null,
    event?.event_type,
    recordVersion,
    'PAUSED_CREATE_APPROVED',
    event?.event_type === 'PAUSED_CREATE_COMPLETED' ? 'PAUSED_CREATED' : 'PAUSED_CREATE_APPROVED',
  ];
  const laterTypes = [
    'PAUSED_CREATE_CLAIMED',
    'PAUSED_CREATE_RECLAIMED',
    'PAUSED_CREATE_FENCED',
    'PAUSED_CREATE_FENCED_FAILED',
    'PAUSED_CREATE_RECONCILIATION_CLAIMED',
    'PAUSED_CREATE_COMPLETED',
    'PAUSED_CREATE_CLAIM_RELEASED',
  ];
  const safeErrors = [
    'contract_mismatch',
    'invalid_create_graph',
    'ledger_write_failed',
    'provider_contract_mismatch',
    'provider_create_failed',
    'provider_not_paused',
    'provider_reconciliation_failed',
    'provider_timeout_unresolved',
    'provider_validation_failed',
  ];
  return event
    && expected
    && (expected[0] === null
      ? event.event_id === `${String(recordVersion).padStart(20, '0')}-${event.event_type.toLowerCase().replaceAll('_', '-')}`
      : event.event_id === expected[0])
    && event.event_type === expected[1]
    && (recordVersion <= 3 || laterTypes.includes(event.event_type))
    && event.record_version === expected[2]
    && event.from_state === expected[3]
    && event.to_state === expected[4]
    && (event.error_code === null || safeErrors.includes(event.error_code))
    && typeof event.occurred_at === 'string';
}

function normalizeStatus(payload) {
  const digest = payload?.contract_hash?.match(/^sha256:([a-f0-9]{64})$/)?.[1];
  const currentIndex = STATES.indexOf(payload?.state);
  const expectedWorkflow = STATES.map((state, index) => [
    state,
    index < currentIndex ? 'complete' : index === currentIndex ? 'current' : 'not_started',
  ]);
  const items = payload?.events?.items;
  const firstVersion = payload?.events?.first_version;
  const safe = payload?.schema_version === 2
    && payload?.deployment_key === 'tho-search-high-intent-huffman-v1'
    && payload?.deployment_id === `${payload.deployment_key}--${digest}`
    && STATES.includes(payload?.state)
    && payload?.state_source === 'FIRESTORE_AUTHORITY_LEDGER'
    && Number.isInteger(payload?.version)
    && payload.version >= currentIndex + 1
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
    && payload.workflow.length === STATES.length
    && payload.workflow.every((step, index) => (
      step?.state === expectedWorkflow[index][0] && step?.status === expectedWorkflow[index][1]
    ))
    && payload?.actions?.server_validation === (payload.state === 'INTERNAL_DRAFT')
    && payload?.paused_create?.activation_authorized === false
    && payload?.paused_create?.spend_enabled === false
    && (currentIndex < 2
      ? payload?.paused_create?.outbox_state === null
      : ['PENDING', 'DISPATCHING', 'DISPATCHED', 'FAILED'].includes(payload?.paused_create?.outbox_state))
    && (payload?.state !== 'PAUSED_CREATED'
      || payload?.paused_create?.outbox_state === 'DISPATCHED')
    && Array.isArray(items)
    && payload?.events?.count === payload.version
    && Number.isInteger(firstVersion)
    && firstVersion === payload.version - items.length + 1
    && items.length === Math.min(payload.version, 100)
    && items.every((event, index) => isSafeEvent(event, index, firstVersion));
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
    paused_create: {
      outbox_state: payload.paused_create.outbox_state,
      activation_authorized: false,
      spend_enabled: false,
    },
    events: {
      count: payload.events.count,
      first_version: firstVersion,
      items: items.map(event => ({
        event_id: event.event_id,
        event_type: event.event_type,
        record_version: event.record_version,
        from_state: event.from_state,
        to_state: event.to_state,
        error_code: event.error_code,
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

function isStrictBooleanMap(value, keys) {
  return value && Object.keys(value).length === keys.length
    && keys.every(key => typeof value[key] === 'boolean');
}

function normalizeApprovalReadiness(payload) {
  const gateKeys = [
    'feature_enabled',
    'cloud_readiness_verified',
    'iam_verified',
    'revision_bound',
    'dispatcher_configured',
  ];
  const digest = payload?.contract_hash?.match(/^sha256:([a-f0-9]{64})$/)?.[1];
  const safe = payload?.schema_version === 1
    && payload?.deployment_id === `tho-search-high-intent-huffman-v1--${digest}`
    && payload?.state === 'SERVER_VALIDATED'
    && payload?.expected_version === 2
    && payload?.budget?.average_daily_usd === 20
    && payload?.budget?.max_single_day_charge_usd === 40
    && payload?.budget?.monthly_charge_limit_usd === 608
    && payload?.budget?.max_cpc_usd === 5
    && isStrictBooleanMap(payload?.gates, gateKeys)
    && (payload?.access_evidence_id === null
      || /^sha256:[a-f0-9]{64}$/.test(payload?.access_evidence_id))
    && typeof payload?.access_evidence_fresh === 'boolean'
    && typeof payload?.action_available === 'boolean'
    && typeof payload?.dispatch_enabled === 'boolean'
    && payload?.paused_only === true
    && payload?.activation_authorized === false
    && payload?.spend_enabled === false
    && Array.isArray(payload?.remediation)
    && payload.remediation.every(item => typeof item === 'string')
    && payload.access_evidence_fresh === (payload.access_evidence_id !== null)
    && payload.action_available === (
      gateKeys.every(key => payload.gates[key])
      && payload.access_evidence_fresh
      && !payload.dispatch_enabled
    );
  if (!safe) throw new Error('PAUSED-only approval readiness is unavailable.');
  return {
    ...payload,
    budget: { ...payload.budget },
    gates: { ...payload.gates },
    remediation: [...payload.remediation],
  };
}

export async function getGoogleAdsPausedCreateApprovalReadiness() {
  try {
    const response = await adminFetch('/api/admin/google-ads/paused-create-approval-readiness');
    if (!response.ok) throw new Error();
    return normalizeApprovalReadiness(await response.json());
  } catch (_error) {
    throw new Error('Sign in with Ari’s owner passkey to review PAUSED creation.');
  }
}

function base64urlToBuffer(value) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const binary = globalThis.atob(value.replace(/-/g, '+').replace(/_/g, '/') + padding);
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

function bufferToBase64url(value) {
  if (value === null || value === undefined) return null;
  const bytes = new Uint8Array(value);
  let binary = '';
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return globalThis.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function assertionPayload(credential) {
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      authenticatorData: bufferToBase64url(credential.response.authenticatorData),
      clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
      signature: bufferToBase64url(credential.response.signature),
      userHandle: bufferToBase64url(credential.response.userHandle),
    },
  };
}

export async function verifyGoogleAdsPausedCreateOwner(readiness) {
  const context = {
    purpose: 'PAUSED_CREATE',
    deployment_id: readiness.deployment_id,
    contract_hash: readiness.contract_hash,
    caps: { ...readiness.budget },
  };
  const begin = await adminFetch('/api/admin/passkey/google-ads-step-up/begin', {
    method: 'POST',
    retry: false,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(context),
  });
  if (!begin.ok) throw new Error('Owner passkey verification could not start.');
  const options = await begin.json();
  options.challenge = base64urlToBuffer(options.challenge);
  options.allowCredentials = (options.allowCredentials || []).map(item => ({
    ...item,
    id: base64urlToBuffer(item.id),
  }));
  const credential = await globalThis.navigator.credentials.get({ publicKey: options });
  const complete = await adminFetch('/api/admin/passkey/google-ads-step-up/complete', {
    method: 'POST',
    retry: false,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context, credential: assertionPayload(credential) }),
  });
  if (!complete.ok) throw new Error('Owner passkey verification failed.');
  const proof = await complete.json();
  if (proof?.verified !== true
    || typeof proof?.proof_reference !== 'string'
    || proof.proof_reference.length < 32
    || proof.proof_reference.length > 4096
    || !/^sha256:[a-f0-9]{64}$/.test(proof?.proof_id)
    || !/^sha256:[a-f0-9]{64}$/.test(proof?.access_evidence_id)
    || proof.access_evidence_id !== readiness.access_evidence_id) {
    throw new Error('Owner passkey proof is unavailable.');
  }
  return {
    proof_reference: proof.proof_reference,
    proof_id: proof.proof_id,
    access_evidence_id: proof.access_evidence_id,
  };
}

export async function approveGoogleAdsPausedCreate(readiness, proof) {
  const response = await adminFetch('/api/admin/google-ads/paused-create-approval', {
    method: 'POST',
    retry: false,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      deployment_id: readiness.deployment_id,
      expected_version: readiness.expected_version,
      proof_reference: proof.proof_reference,
      proof_id: proof.proof_id,
      access_evidence_id: proof.access_evidence_id,
    }),
  });
  if (!response.ok) throw new Error('PAUSED-only approval failed. Refresh and retry.');
  const payload = await response.json();
  if (payload?.state !== 'PAUSED_CREATE_APPROVED'
    || payload?.deployment_id !== readiness.deployment_id
    || payload?.contract_hash !== readiness.contract_hash
    || !Number.isInteger(payload?.version)
    || payload.version < 3
    || payload?.outbox_state !== 'PENDING'
    || typeof payload?.replayed !== 'boolean'
    || payload?.paused_only !== true
    || payload?.activation_authorized !== false
    || payload?.spend_enabled !== false) {
    throw new Error('PAUSED-only approval response is unavailable.');
  }
  return payload;
}
