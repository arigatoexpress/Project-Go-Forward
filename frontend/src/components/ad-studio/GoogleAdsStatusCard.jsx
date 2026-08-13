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

function readinessMatchesStatus(readiness, status) {
  return readiness.deployment_id === status.deployment_id
    && readiness.contract_hash === status.contract_hash
    && readiness.expected_version === status.version;
}

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
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);

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
      if (!readinessMatchesStatus(readiness, nextStatus)) {
        setApprovalReadiness(null);
        setApprovalRemediation('The reviewed contract changed. Refresh before approval.');
        return;
      }
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

  useEffect(() => {
    setReviewAcknowledged(false);
  }, [status?.contract_hash]);

  useEffect(() => {
    const expires = Date.parse(approvalReadiness?.account_connection?.expires_at);
    if (!Number.isFinite(expires)) return undefined;
    const timer = globalThis.setTimeout(() => {
      setApprovalReadiness(null);
      setApprovalRemediation(
        'The read-only account and USD evidence expired. Refresh before approval.',
      );
    }, Math.max(0, expires - Date.now()));
    return () => globalThis.clearTimeout(timer);
  }, [approvalReadiness?.account_connection?.expires_at]);

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
    if (!approvalReadiness?.action_available || !reviewAcknowledged || approving) return;
    setApproving(true);
    setApprovalError('');
    try {
      const refreshedReadiness = await getGoogleAdsPausedCreateApprovalReadiness();
      if (
        !readinessMatchesStatus(refreshedReadiness, status)
        || refreshedReadiness.access_evidence_id !== approvalReadiness.access_evidence_id
        || !refreshedReadiness.action_available
      ) {
        setApprovalReadiness(refreshedReadiness);
        throw new Error('PAUSED-only approval prerequisites changed. Review and retry.');
      }
      const confirmed = globalThis.confirm(
        'This authorizes creation of PAUSED Google Ads resources. If the separate dispatcher is already runnable, creation may begin after approval. It cannot activate ads or spend money. Continue?',
      );
      if (!confirmed) return;
      const proof = await verifyGoogleAdsPausedCreateOwner(refreshedReadiness);
      const result = await approveGoogleAdsPausedCreate(refreshedReadiness, proof);
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
  const review = status.campaign_review;
  const connection = approvalReadiness?.account_connection;
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

      <article className="google-ads-status-panel" aria-labelledby="campaign-review-heading">
        <h2 id="campaign-review-heading">Exact PAUSED campaign review</h2>
        <p className="google-ads-status-note">
          This server-owned artifact is bound to the content hash below. It contains no account
          identifier, credential, provider resource, or activation authority.
        </p>
        <dl className="google-ads-status-details">
          <div><dt>Campaign</dt><dd>{review.campaign_name}</dd></div>
          <div><dt>Serving state</dt><dd>{review.status} · {review.channel} · {review.currency_code}</dd></div>
          <div>
            <dt>Location</dt>
            <dd>
              {review.geo.radius_miles}-mile presence-only radius around
              {' '}{review.geo.center.address}, {review.geo.center.city}, {review.geo.center.state}
              {' '}({review.geo.center.latitude}, {review.geo.center.longitude}); no postal-code targeting
            </dd>
          </div>
          <div>
            <dt>Networks</dt>
            <dd>Google Search only · Search Partners off · Display off</dd>
          </div>
          <div><dt>Bidding</dt><dd>Maximize clicks · ${review.bidding.max_cpc_usd} maximum CPC</dd></div>
          <div>
            <dt>Tracking template</dt>
            <dd>
              utm_source={review.tracking.utm_source} · utm_medium={review.tracking.utm_medium}
              {' '}· utm_campaign={review.tracking.utm_campaign} ·
              {' '}utm_content={review.tracking.utm_content} · utm_term={review.tracking.utm_term}
            </dd>
          </div>
          <div>
            <dt>Housing posture</dt>
            <dd>
              All age, gender, and parental-status groups enabled; no audiences, customer match,
              demographic exclusions, marital-status, or postal-code targeting. Google Ads policy
              acknowledgment is still required before activation.
            </dd>
          </div>
        </dl>

        <div className="google-ads-review-groups">
          {review.ad_groups.map(group => (
            <details key={group.name} className="google-ads-review-detail" open>
              <summary>{group.name} · {group.status}</summary>
              <p><strong>Landing URL:</strong> {group.responsive_search_ad.final_url}</p>
              <p><strong>Display path:</strong> /{group.responsive_search_ad.path1}/{group.responsive_search_ad.path2}</p>
              <p><strong>Keywords:</strong></p>
              <ul>
                {group.keywords.map(keyword => (
                  <li key={`${keyword.text}-${keyword.match_type}`}>
                    {keyword.text} <small>({keyword.match_type.toLowerCase()})</small>
                  </li>
                ))}
              </ul>
              <p><strong>Headlines:</strong></p>
              <ul>{group.responsive_search_ad.headlines.map(text => <li key={text}>{text}</li>)}</ul>
              <p><strong>Descriptions:</strong></p>
              <ul>{group.responsive_search_ad.descriptions.map(text => <li key={text}>{text}</li>)}</ul>
            </details>
          ))}
          <details className="google-ads-review-detail">
            <summary>Negative keywords · {review.negative_keywords.length}</summary>
            <ul>
              {review.negative_keywords.map(keyword => (
                <li key={`${keyword.text}-${keyword.match_type}`}>
                  {keyword.text} <small>({keyword.match_type.toLowerCase()})</small>
                </li>
              ))}
            </ul>
          </details>
        </div>

        <div className="google-ads-review-warning">
          <strong>Pre-activation holds</strong>
          <p>
            Conversion goals are not attached by PAUSED creation. Import and verify
            {' '}{review.conversion_intent.events.map(event => event.name).join(' and ')} before
            any future activation; no activation control exists here.
          </p>
          <p>
            Stop-loss intent: pause on any 7-day threshold—$200 spend or 100 clicks with zero
            reachable leads, or CPA above $150 after at least 3 reachable leads. This is reviewed
            intent and a hard hold, not implemented protection.
          </p>
        </div>
        <label className="google-ads-review-acknowledgement">
          <input
            type="checkbox"
            checked={reviewAcknowledged}
            disabled={approvalTerminal || Boolean(approvalResult)}
            onChange={event => setReviewAcknowledged(event.target.checked)}
          />
          <span>I reviewed this exact PAUSED copy, targeting, limits, and pre-activation holds.</span>
        </label>
      </article>

      <article className="google-ads-status-panel" aria-labelledby="owner-verification-heading">
        <h2 id="owner-verification-heading">Owner PAUSED-create approval</h2>
        <p>
          Ari’s freshly verified owner passkey is single-use and bound to this exact
          checked-in contract, current access evidence, and reviewed limits.
        </p>
        <dl className="google-ads-status-details">
          <div><dt>Content hash</dt><dd>{status.contract_hash}</dd></div>
          <div><dt>Creation mode</dt><dd>PAUSED only · $0 spend</dd></div>
          {approvalReadiness && (
            <div>
              <dt>Storefront dispatch flag</dt>
              <dd>{approvalReadiness.dispatch_enabled ? 'Enabled' : 'Disabled'}</dd>
            </div>
          )}
          {connection && (
            <>
              <div>
                <dt>Account connection</dt>
                <dd>Exact configured account + USD verified by read-only probe</dd>
              </div>
              <div><dt>Evidence digest</dt><dd>{connection.evidence_digest}</dd></div>
              <div>
                <dt>Evidence window</dt>
                <dd>
                  Observed {new Date(connection.observed_at).toLocaleString()}; expires
                  {' '}{new Date(connection.expires_at).toLocaleString()}
                </dd>
              </div>
              <div><dt>Evidence revision</dt><dd>{connection.source_revision}</dd></div>
            </>
          )}
          {approvalReadiness && !connection && (
            <div><dt>Account connection</dt><dd>No fresh read-only account + USD evidence</dd></div>
          )}
        </dl>
        {approvalReadiness && (
          <ul className="google-ads-gate-list" aria-label="PAUSED creation prerequisites">
            <li>Owner approval config: {approvalReadiness.gates.feature_enabled ? 'verified' : 'not verified'}</li>
            <li>Cloud readiness: {approvalReadiness.gates.cloud_readiness_verified ? 'verified' : 'not verified'}</li>
            <li>Least-privilege IAM: {approvalReadiness.gates.iam_verified ? 'verified' : 'not verified'}</li>
            <li>Candidate revision binding: {approvalReadiness.gates.revision_bound ? 'verified' : 'not verified'}</li>
            <li>Fixed dispatcher target: {approvalReadiness.gates.dispatcher_configured ? 'verified' : 'not verified'}</li>
          </ul>
        )}
        <button
          className="google-ads-validation-button"
          type="button"
          disabled={
            !approvalReadiness?.action_available
            || !reviewAcknowledged
            || approving
            || Boolean(approvalResult)
          }
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
          <>
            {!reviewAcknowledged && (
              <p className="google-ads-actions-locked">Review and acknowledge the exact campaign artifact above.</p>
            )}
            <p className="google-ads-status-note">
              Approval queues PAUSED-only work. This storefront flag does not prove whether
              the separate dispatcher job or a scheduler is runnable; verify external runtime
              state before approving.
            </p>
          </>
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
