import { useEffect, useState } from 'react';
import { CircleAlert, CircleCheck, Search, ShieldCheck } from 'lucide-react';

import {
  ensureGoogleAdsInternalDraft,
  runGoogleAdsServerValidation,
} from '../../api/googleAdsAdmin';
import './GoogleAdsStatusCard.css';

const STATE_LABELS = {
  INTERNAL_DRAFT: 'Internal draft',
  SERVER_VALIDATED: 'Server validated',
};
const STATUS_LABELS = { current: 'Current', not_started: 'Not started', complete: 'Complete' };

export default function GoogleAdsStatusCard() {
  const [status, setStatus] = useState(null);
  const [failed, setFailed] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState(false);
  const [validationKey, setValidationKey] = useState(null);

  useEffect(() => {
    let mounted = true;
    ensureGoogleAdsInternalDraft()
      .then(payload => { if (mounted) setStatus(payload); })
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
      setStatus(await runGoogleAdsServerValidation(status, requestKey));
      setValidationKey(null);
    } catch (_error) {
      setValidationError(true);
    } finally {
      setValidating(false);
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
