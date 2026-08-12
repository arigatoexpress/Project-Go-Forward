import { useEffect, useState } from 'react';
import { CircleAlert, CircleCheck, LockKeyhole, Search } from 'lucide-react';

import { getGoogleAdsDeploymentReadiness } from '../../api/googleAdsAdmin';
import './GoogleAdsStatusCard.css';

const STATE_LABELS = {
  INTERNAL_DRAFT: 'Internal draft',
  SERVER_VALIDATED: 'Server validated',
  PAUSED_CREATE_APPROVED: 'Paused create approved',
  PAUSED_CREATED: 'Paused created',
};

const STATUS_LABELS = {
  current: 'Current',
  not_started: 'Not started',
  locked: 'Locked',
};

export default function GoogleAdsStatusCard() {
  const [status, setStatus] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let mounted = true;
    getGoogleAdsDeploymentReadiness()
      .then((payload) => {
        if (mounted) setStatus(payload);
      })
      .catch(() => {
        if (mounted) setFailed(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  if (failed) {
    return (
      <div className="google-ads-status-message is-error" role="alert">
        <CircleAlert aria-hidden="true" size={20} />
        <span>Paid Search status is unavailable.</span>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="google-ads-status-message" role="status" aria-live="polite">
        <span className="google-ads-status-spinner" aria-hidden="true" />
        <span>Loading Paid Search status…</span>
      </div>
    );
  }

  const shortHash = `${status.contract_hash.slice(0, 15)}…`;

  return (
    <section className="google-ads-status-card" aria-labelledby="paid-search-heading">
      <header className="google-ads-status-header">
        <div className="google-ads-status-icon" aria-hidden="true">
          <Search size={24} />
        </div>
        <div>
          <p className="google-ads-status-eyebrow">Google Ads · read only</p>
          <h1 id="paid-search-heading">Paid Search</h1>
          <p>Checked-in campaign contract status. No Google Ads account was contacted.</p>
        </div>
      </header>

      <div className="google-ads-status-grid">
        <article className="google-ads-status-panel" aria-labelledby="account-status-heading">
          <h2 id="account-status-heading">Account status</h2>
          <div className="google-ads-status-callout">
            <CircleAlert aria-hidden="true" size={19} />
            <div>
              <strong>Account access not verified</strong>
              <p>No sanitized connection evidence has been recorded.</p>
            </div>
          </div>
          <dl className="google-ads-status-details">
            <div>
              <dt>Deployment</dt>
              <dd>{status.deployment_key}</dd>
            </div>
            <div>
              <dt>Contract</dt>
              <dd title={status.contract_hash}>{shortHash}</dd>
            </div>
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
        <div className="google-ads-workflow-heading">
          <h2 id="workflow-heading">Deployment workflow</h2>
          <span className="google-ads-locked-badge">
            <LockKeyhole aria-hidden="true" size={14} />
            Actions locked
          </span>
        </div>
        <ol className="google-ads-workflow">
          {status.workflow.map((step) => (
            <li key={step.state} className={`is-${step.status}`}>
              <CircleCheck aria-hidden="true" size={18} />
              <span>
                <strong>{STATE_LABELS[step.state]}</strong>
                <small>{STATUS_LABELS[step.status]}</small>
              </span>
            </li>
          ))}
        </ol>
        <p className="google-ads-actions-locked">All campaign and spend actions are locked.</p>
      </article>
    </section>
  );
}
