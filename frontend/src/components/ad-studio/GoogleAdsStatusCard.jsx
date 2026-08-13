import { useEffect, useState } from 'react';
import { CircleAlert, CircleCheck, Search, ShieldCheck } from 'lucide-react';

import {
  approveGoogleAdsPausedCreate,
  ensureGoogleAdsInternalDraft,
  getGoogleAdsDeploymentReadiness,
  getGoogleAdsPausedCreateApprovalReadiness,
  runGoogleAdsServerValidation,
  verifyGoogleAdsPausedCreateOwner,
} from '../../api/googleAdsAdmin';
import './GoogleAdsStatusCard.css';

const STATE_LABELS = {
  INTERNAL_DRAFT: 'Internal draft',
  SERVER_VALIDATED: 'Server validated',
  PAUSED_CREATE_APPROVED: 'PAUSED creation approved',
  PAUSED_CREATED: 'PAUSED resources created',
};
const STATUS_LABELS = { current: 'Current', not_started: 'Not started', complete: 'Complete' };

export default function GoogleAdsStatusCard() {
  const [status, setStatus] = useState(null);
  const [failed, setFailed] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState(false);
  const [validationKey, setValidationKey] = useState(null);
  const [approvalReadiness, setApprovalReadiness] = useState(null);
  const [approvalRemediation, setApprovalRemediation] = useState('Complete offline server validation first.');
  const [approving, setApproving] = useState(false);
  const [approvalResult, setApprovalResult] = useState(null);
  const [approvalError, setApprovalError] = useState('');

  async function refreshApprovalReadiness(nextStatus) {
    if (nextStatus?.state === 'PAUSED_CREATE_APPROVED') {
      setApprovalReadiness(null);
      const outbox = nextStatus.paused_create?.outbox_state?.toLowerCase() || 'recorded';
      setApprovalRemediation(nextStatus.paused_create?.outbox_state === 'FAILED'
        ? 'PAUSED-only creation remains approved, but the bounded dispatcher attempts are exhausted. Keep the feature disabled and review the sanitized job evidence. No spend is enabled.'
        : `PAUSED-only creation is approved; durable outbox is ${outbox}. No spend is enabled.`);
      return;
    }
    if (nextStatus?.state === 'PAUSED_CREATED') {
      setApprovalReadiness(null);
      setApprovalRemediation(
        'PAUSED Google Ads resources were created and reconciled. They remain inactive; no spend is enabled.',
      );
      return;
    }
    if (nextStatus?.state !== 'SERVER_VALIDATED') {
      setApprovalReadiness(null);
      setApprovalRemediation('Complete offline server validation first.');
      return;
    }
    try {
      const readiness = await getGoogleAdsPausedCreateApprovalReadiness();
      setApprovalReadiness(readiness);
      setApprovalRemediation(readiness.remediation.join(' '));
    } catch (error) {
      setApprovalReadiness(null);
      setApprovalRemediation(error.message);
    }
  }

  useEffect(() => {
    let mounted = true;
    ensureGoogleAdsInternalDraft()
      .then(payload => {
        if (mounted) {
          setStatus(payload);
          refreshApprovalReadiness(payload);
        }
      })
      .catch(() => { if (mounted) setFailed(true); });
    return () => { mounted = false; };
  }, []);

  async function runValidation() {
    if (!status?.actions.server_validation || validating) return;
    setValidating(true);
    setValidationError(false);
    const requestKey = validationKey || `offline-validation-${globalThis.crypto.randomUUID()}`;
    setValidationKey(requestKey);
    try {
      const nextStatus = await runGoogleAdsServerValidation(status, requestKey);
      setStatus(nextStatus);
      await refreshApprovalReadiness(nextStatus);
      setValidationKey(null);
    } catch (_error) {
      setValidationError(true);
    } finally {
      setValidating(false);
    }
  }

  async function approvePausedCreate() {
    if (!approvalReadiness?.action_available || approving) return;
    const confirmed = globalThis.confirm(
      'This approves creation of PAUSED Google Ads resources only. It cannot activate ads or spend money. Continue?',
    );
    if (!confirmed) return;
    setApproving(true);
    setApprovalError('');
    try {
      const proof = await verifyGoogleAdsPausedCreateOwner(approvalReadiness);
      const result = await approveGoogleAdsPausedCreate(approvalReadiness, proof);
      setApprovalResult(result);
      try {
        const terminalStatus = await getGoogleAdsDeploymentReadiness();
        setStatus(terminalStatus);
        await refreshApprovalReadiness(terminalStatus);
      } catch (_refreshError) {
        setApprovalError(
          'PAUSED-only approval was recorded, but durable status refresh is unavailable. Refresh before taking any further action.',
        );
      }
    } catch (error) {
      setApprovalError(error.message || 'PAUSED-only approval failed. Refresh and retry.');
    } finally {
      setApproving(false);
    }
  }

  if (failed) return (
    <div className="google-ads-status-message is-error" role="alert">
      <CircleAlert aria-hidden="true" size={20} />
      <span>Paid Search status is unavailable.</span>
    </div>
  );
  if (!status) return (
    <div className="google-ads-status-message" role="status" aria-live="polite">
      <span className="google-ads-status-spinner" aria-hidden="true" />
      <span>Loading Paid Search status…</span>
    </div>
  );

  const eventLabel = `${status.events.count} recorded event${status.events.count === 1 ? '' : 's'}`;
  const validated = status.state === 'SERVER_VALIDATED';
  const approvalTerminal = ['PAUSED_CREATE_APPROVED', 'PAUSED_CREATED'].includes(status.state);
  return (
    <section className="google-ads-status-card" aria-labelledby="paid-search-heading">
      <header className="google-ads-status-header">
        <div className="google-ads-status-icon" aria-hidden="true"><Search size={24} /></div>
        <div>
          <p className="google-ads-status-eyebrow">Google Ads · offline review</p>
          <h1 id="paid-search-heading">Paid Search</h1>
          <p>Durable contract review. No Google Ads account is contacted by this action.</p>
        </div>
      </header>

      <div className="google-ads-status-grid">
        <article className="google-ads-status-panel" aria-labelledby="durable-status-heading">
          <h2 id="durable-status-heading">Durable review state</h2>
          <div className="google-ads-status-callout">
            <ShieldCheck aria-hidden="true" size={19} />
            <div><strong>{STATE_LABELS[status.state]}</strong><p>{eventLabel}</p></div>
          </div>
          <dl className="google-ads-status-details">
            <div><dt>Deployment</dt><dd>{status.deployment_key}</dd></div>
            <div><dt>Ledger version</dt><dd>{status.version}</dd></div>
          </dl>
        </article>
        <article className="google-ads-status-panel" aria-labelledby="budget-heading">
          <h2 id="budget-heading">Reviewed limits</h2>
          <p className="google-ads-budget-summary">
            $20 average daily budget, up to $40 in a single day, $608 monthly charging
            limit, and $5 maximum CPC.
          </p>
          <p className="google-ads-status-note">These values describe the inert contract only.</p>
        </article>
      </div>

      <article className="google-ads-status-panel" aria-labelledby="owner-verification-heading">
        <h2 id="owner-verification-heading">Owner PAUSED-create approval</h2>
        <p>
          Ari’s freshly verified owner passkey is single-use and bound to this exact
          checked-in contract, current access evidence, and reviewed limits.
        </p>
        <dl className="google-ads-status-details">
          <div><dt>Content hash</dt><dd>{status.contract_hash}</dd></div>
          <div><dt>Creation mode</dt><dd>PAUSED only · $0 spend</dd></div>
        </dl>
        <button
          className="google-ads-validation-button"
          type="button"
          disabled={!approvalReadiness?.action_available || approving || Boolean(approvalResult)}
          onClick={approvePausedCreate}
        >
          {approving ? 'Verifying owner…' : 'Verify owner and approve PAUSED creation'}
        </button>
        {!approvalReadiness?.action_available && (
          <p
            className={approvalTerminal ? 'google-ads-validation-success' : 'google-ads-actions-locked'}
            role="status"
          >
            {approvalRemediation}
          </p>
        )}
        {approvalReadiness?.action_available && !approvalResult && (
          <p className="google-ads-status-note">
            Approval queues PAUSED-only work. The fixed dispatcher remains separately
            feature- and cloud-gated.
          </p>
        )}
        {approvalResult && (
          <p className="google-ads-validation-success" role="status">
            {status.state === 'PAUSED_CREATED'
              ? 'PAUSED resources were created and reconciled. They remain inactive; no spend is enabled.'
              : status.state === 'PAUSED_CREATE_APPROVED'
                ? `PAUSED-only creation approved. Durable outbox ${status.paused_create.outbox_state.toLowerCase()}; no spend is enabled.`
                : 'PAUSED-only approval recorded. Refresh durable status before any further action; no spend is enabled.'}
          </p>
        )}
        {approvalError && <p className="google-ads-validation-error" role="alert">{approvalError}</p>}
      </article>

      <article className="google-ads-status-panel" aria-labelledby="workflow-heading">
        <h2 id="workflow-heading">Offline review workflow</h2>
        <ol className="google-ads-workflow">
          {status.workflow.map(step => (
            <li key={step.state} className={`is-${step.status}`}>
              <CircleCheck aria-hidden="true" size={18} />
              <span><strong>{STATE_LABELS[step.state]}</strong><small>{STATUS_LABELS[step.status]}</small></span>
            </li>
          ))}
        </ol>
        <button
          className="google-ads-validation-button"
          type="button"
          disabled={!status.actions.server_validation || validating}
          onClick={runValidation}
        >
          {validating ? 'Validating offline…' : 'Run offline server validation'}
        </button>
        {validated && <p className="google-ads-validation-success" role="status">Offline server validation complete.</p>}
        {validationError && <p className="google-ads-validation-error" role="alert">Offline server validation failed. Retry when ready.</p>}
        <p className="google-ads-actions-locked">Campaign and spend operations remain unavailable.</p>
      </article>
    </section>
  );
}
