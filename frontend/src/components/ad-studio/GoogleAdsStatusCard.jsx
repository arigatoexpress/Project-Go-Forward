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
import {
  EXAMPLE_BRIEF,
  GATE_GUIDANCE,
  OWNER_ONLY_STEPS,
  STAFF_CAMPAIGN_NAME,
  STATE_LABELS,
  STATUS_LABELS,
  adPreview,
  briefProblem,
  budgetPlain,
  checkButtonLabel,
  launchResultCopy,
  locationPlain,
  plainRemediation,
  progressPlain,
  readStaffBrief,
  terminalRemediation,
  writeStaffBrief,
} from './googleAdsGuidance';
import './GoogleAdsStatusCard.css';

const CHECK_FIRST = 'Check the campaign before you launch it.';

function readinessMatchesStatus(readiness, status) {
  return readiness.deployment_id === status.deployment_id
    && readiness.contract_hash === status.contract_hash
    && readiness.expected_version === status.version;
}

export default function GoogleAdsStatusCard() {
  const [status, setStatus] = useState(null);
  const [failed, setFailed] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [validationKey, setValidationKey] = useState(null);
  const [approvalReadiness, setApprovalReadiness] = useState(null);
  const [approvalRemediation, setApprovalRemediation] = useState(CHECK_FIRST);
  const [approving, setApproving] = useState(false);
  const [approvalResult, setApprovalResult] = useState(null);
  const [approvalError, setApprovalError] = useState('');
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);
  const [brief, setBrief] = useState(readStaffBrief);
  const [briefAccepted, setBriefAccepted] = useState(false);
  const [briefNotice, setBriefNotice] = useState('');

  async function refreshApprovalReadiness(nextStatus) {
    if (nextStatus?.state === 'PAUSED_CREATE_APPROVED' || nextStatus?.state === 'PAUSED_CREATED') {
      setApprovalReadiness(null);
      setApprovalRemediation(terminalRemediation(nextStatus));
      return;
    }
    if (nextStatus?.state !== 'SERVER_VALIDATED') {
      setApprovalReadiness(null);
      setApprovalRemediation(CHECK_FIRST);
      return;
    }
    try {
      const readiness = await getGoogleAdsPausedCreateApprovalReadiness();
      if (!readinessMatchesStatus(readiness, nextStatus)) {
        setApprovalReadiness(null);
        setApprovalRemediation('The campaign on this page does not match the one that was checked. Refresh the page and review it again.');
        return;
      }
      setApprovalReadiness(readiness);
      setApprovalRemediation(
        plainRemediation(readiness.remediation)
        || 'Review the preview, then the owner can launch the campaign turned off.',
      );
    } catch (error) {
      setApprovalReadiness(null);
      setApprovalRemediation(error.message);
    }
  }

  async function loadDraft() {
    setFailed(false);
    try {
      const payload = await ensureGoogleAdsInternalDraft();
      setStatus(payload);
      await refreshApprovalReadiness(payload);
    } catch {
      setFailed(true);
    }
  }

  useEffect(() => {
    let mounted = true;
    ensureGoogleAdsInternalDraft()
      .then(payload => {
        if (!mounted) return;
        setStatus(payload);
        refreshApprovalReadiness(payload);
      })
      .catch(() => { if (mounted) setFailed(true); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    setReviewAcknowledged(false);
    setBriefAccepted(false);
  }, [status?.contract_hash]);

  useEffect(() => {
    writeStaffBrief(brief);
  }, [brief]);

  useEffect(() => {
    const expires = Date.parse(approvalReadiness?.account_connection?.expires_at);
    if (!Number.isFinite(expires)) return undefined;
    const evaluated = Date.parse(approvalReadiness?.evaluated_at);
    if (!Number.isFinite(evaluated) || expires <= evaluated) return undefined;
    const timer = globalThis.setTimeout(() => {
      setApprovalReadiness(null);
      setApprovalRemediation(
        'The account check expired. Refresh the page before the owner launches.',
      );
    }, expires - evaluated);
    return () => globalThis.clearTimeout(timer);
  }, [approvalReadiness?.account_connection?.expires_at, approvalReadiness?.evaluated_at]);

  const approvalTerminal = ['PAUSED_CREATE_APPROVED', 'PAUSED_CREATED'].includes(status?.state);

  async function createPreview() {
    if (!status || validating || approvalTerminal) return;
    const problem = briefProblem(brief);
    if (problem) {
      setBriefNotice(problem);
      setBriefAccepted(false);
      return;
    }
    setBriefNotice('');
    if (!status?.actions.server_validation) {
      setBriefAccepted(true);
      return;
    }
    setValidating(true);
    setValidationError('');
    const requestKey = validationKey || `offline-validation-${globalThis.crypto.randomUUID()}`;
    setValidationKey(requestKey);
    try {
      const nextStatus = await runGoogleAdsServerValidation(status, requestKey);
      setStatus(nextStatus);
      await refreshApprovalReadiness(nextStatus);
      setValidationKey(null);
      setBriefAccepted(true);
    } catch (error) {
      setValidationError(error.message || 'We could not check this campaign. Nothing was sent to Google. Try again.');
    } finally {
      setValidating(false);
    }
  }

  async function approvePausedCreate() {
    if (!approvalReadiness?.action_available || !reviewAcknowledged || !briefAccepted || approving) return;
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
        throw new Error('Launch prerequisites changed. Review the campaign and try again.');
      }
      const confirmed = globalThis.confirm(
        'This creates the Google Ads campaign turned off. If the background job is already running, Google may create it right after the owner confirms. The ads stay off and this does not spend money. Continue?',
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
          'The launch was recorded, but we could not refresh the status. Refresh the page before doing anything else. No money was spent.',
        );
      }
    } catch (error) {
      setApprovalError(error.message || 'The launch did not go through. Refresh the page and try again. No money was spent.');
    } finally {
      setApproving(false);
    }
  }

  if (failed) return (
    <div className="google-ads-status-message is-error" role="alert">
      <CircleAlert aria-hidden="true" size={20} />
      <div>
        <p>Google Ads is unavailable right now. Nothing was sent to Google.</p>
        <button className="google-ads-validation-button" type="button" onClick={loadDraft}>
          Load again
        </button>
      </div>
    </div>
  );
  if (!status) return (
    <div className="google-ads-status-message" role="status" aria-live="polite">
      <span className="google-ads-status-spinner" aria-hidden="true" />
      <span>Loading Google Ads…</span>
    </div>
  );

  const review = status.campaign_review;
  const connection = approvalReadiness?.account_connection;
  const checked = status.state !== 'INTERNAL_DRAFT';
  const launchDisabled = !approvalReadiness?.action_available
    || !reviewAcknowledged
    || !briefAccepted
    || approving
    || Boolean(approvalResult)
    || approvalTerminal;

  return (
    <section className="google-ads-status-card" aria-labelledby="google-ads-heading">
      <header className="google-ads-status-header">
        <div className="google-ads-status-icon" aria-hidden="true"><Search size={24} /></div>
        <div>
          <p className="google-ads-status-eyebrow">Google Ads</p>
          <h1 id="google-ads-heading">Google Ads</h1>
          <p>
            Describe the ad in everyday words, preview how it can look on Google, then launch it
            turned off. Launching does not spend money, and this screen cannot turn ads on.
          </p>
        </div>
      </header>

      <div className="google-ads-status-callout">
        <ShieldCheck aria-hidden="true" size={19} />
        <div>
          <strong>{STATE_LABELS[status.state]}</strong>
          <p>{progressPlain(status)}</p>
        </div>
      </div>

      <article className="google-ads-status-panel" aria-labelledby="owner-setup-heading">
        <h2 id="owner-setup-heading">Before ads can actually run</h2>
        <p>
          You can prepare this campaign and launch it turned off. Only the owner can make the
          ads run, and only in Google Ads — not from this screen.
        </p>
        <ol className="google-ads-owner-steps">
          {OWNER_ONLY_STEPS.map(step => (
            <li key={step.title}>
              <strong>{step.title}.</strong> {step.detail}
            </li>
          ))}
        </ol>
      </article>

      <article className="google-ads-status-panel" aria-labelledby="create-heading">
        <h2 id="create-heading">1. Create</h2>
        <p>
          In a sentence or two, say who should see this ad and what you want them to do.
          Your description does not change the reviewed ad text. Google receives only
          the reviewed headlines and descriptions in the preview.
        </p>
        {approvalTerminal ? (
          <p className="google-ads-status-note">
            This campaign has already been submitted turned off. The preview below is the ad
            Google is holding. Your description is not sent anywhere.
          </p>
        ) : (
          <>
            <label className="google-ads-brief" htmlFor="google-ads-brief">
              Who should see this ad?
            </label>
            <textarea
              id="google-ads-brief"
              value={brief}
              rows={4}
              onChange={event => {
                setBrief(event.target.value);
                setBriefAccepted(false);
                setBriefNotice('');
              }}
            />
            <p className="google-ads-status-note" id="google-ads-brief-hint">
              Example: {EXAMPLE_BRIEF}
            </p>
            <div className="google-ads-action-row">
              <button
                className="google-ads-text-button"
                type="button"
                onClick={() => {
                  setBrief(EXAMPLE_BRIEF);
                  setBriefAccepted(false);
                  setBriefNotice('');
                }}
              >
                Use the example
              </button>
              <button
                className="google-ads-validation-button"
                type="button"
                disabled={validating || (briefAccepted && !status.actions.server_validation)}
                onClick={createPreview}
              >
                {validating ? 'Checking the campaign…' : checkButtonLabel(status.actions.server_validation)}
              </button>
            </div>
            <p className="google-ads-status-note">
              Checking confirms the ad on this website. Your description stays here.
              Nothing is sent to Google, and this does not spend money.
            </p>
            {briefNotice && <p className="google-ads-validation-error" role="alert">{briefNotice}</p>}
            {briefAccepted && !validating && (
              <p className="google-ads-validation-success" role="status">
                {checked
                  ? 'This campaign is checked. Your description stays on this screen and is not sent to Google. Review the preview, then launch it turned off.'
                  : 'Your description is saved on this screen only.'}
              </p>
            )}
            {validationError && <p className="google-ads-validation-error" role="alert">{validationError}</p>}
          </>
        )}
      </article>

      <article className="google-ads-status-panel" aria-labelledby="preview-heading">
        <h2 id="preview-heading">2. Preview</h2>
        <p className="google-ads-status-note">
          These are the {STAFF_CAMPAIGN_NAME}, still turned off. This is a preview, not a live ad.
          Google may rearrange the lines.
        </p>
        <p>{locationPlain(review.geo)}</p>
        <p>{budgetPlain(status.budget)}</p>
        <p>
          Google will try to get the most clicks, with a ${review.bidding.max_cpc_usd} maximum per click.
        </p>
        {briefAccepted && (
          <p className="google-ads-staff-note">
            <strong>Your note, not sent to Google:</strong> {brief.trim()}
          </p>
        )}
        <div className="google-ads-preview-list">
          {review.ad_groups.map(group => {
            const preview = adPreview(group);
            return (
              <article key={group.name} className="google-ads-preview" aria-label={`Preview: ${group.name}`}>
                <p className="google-ads-preview-kicker">{group.name} · turned off</p>
                <p className="google-ads-preview-url"><strong>Ad</strong> {preview.displayUrl}</p>
                <h3>{preview.headline}</h3>
                <p>{preview.description}</p>
                <p className="google-ads-preview-meta">Other lines Google may use: {preview.headlines.join(' · ')}</p>
                <p className="google-ads-preview-meta">More description: {preview.descriptions.join(' ')}</p>
                <p className="google-ads-preview-meta">Sends people to {preview.finalUrl}</p>
                <p className="google-ads-preview-meta">Shows for searches such as {preview.keywords.join(', ')}.</p>
              </article>
            );
          })}
        </div>
        <details className="google-ads-review-detail">
          <summary>Searches that should not show this ad · {review.negative_keywords.length}</summary>
          <p>If someone searches one of these phrases, this ad stays hidden.</p>
          <ul>
            {review.negative_keywords.map(keyword => (
              <li key={`${keyword.text}-${keyword.match_type}`}>{keyword.text}</li>
            ))}
          </ul>
        </details>
        <div className="google-ads-review-warning">
          <strong>Still required before ads can run</strong>
          <p>
            Tour bookings and lead form fills are not attached yet. The owner must confirm
            conversion tracking before ads can ever be turned on. This screen cannot turn ads on.
          </p>
          <p>
            If the owner turns ads on later, the plan is to pause them after 7 days when spend
            reaches ${review.stop_loss.zero_reachable_leads_spend_usd} or clicks reach {review.stop_loss.zero_reachable_leads_clicks} with
            no leads we can contact, or when a lead we can contact costs more than ${review.stop_loss.max_reachable_lead_cpa_usd}.
            That pause is not automatic yet.
          </p>
          <p>
            The ad is allowed for every age, every gender, and people with or without children.
            It does not single out marital status, ZIP codes, or existing customer lists.
            The owner still has to accept Google’s rules for housing ads before ads can be turned on.
          </p>
        </div>
        {!approvalTerminal && (
          <label className="google-ads-review-acknowledgement">
            <input
              type="checkbox"
              checked={reviewAcknowledged}
              disabled={!briefAccepted || Boolean(approvalResult)}
              onChange={event => setReviewAcknowledged(event.target.checked)}
            />
            <span>I read both ads, the Huffman area, and the budget. Launching leaves the ads off and does not spend money.</span>
          </label>
        )}
      </article>

      <article className="google-ads-status-panel" aria-labelledby="launch-heading">
        <h2 id="launch-heading">3. Launch</h2>
        <p>
          Launching asks the owner’s passkey to create this campaign in Google Ads turned off.
          It does not turn the ads on, change billing, or spend money.
        </p>
        {approvalReadiness && (
          <ul className="google-ads-gate-list" aria-label="Launch checklist">
            {Object.entries(GATE_GUIDANCE).map(([key, copy]) => (
              <li key={key}>{approvalReadiness.gates[key] ? copy.ok : copy.missing}</li>
            ))}
            <li>
              {connection
                ? 'Google Ads account is linked and uses US dollars.'
                : 'The Google Ads account is not confirmed yet. The owner needs to link it and show that it uses US dollars.'}
            </li>
          </ul>
        )}
        {connection && (
          <p className="google-ads-status-note">
            This account check is good until {new Date(connection.expires_at).toLocaleString()}.
          </p>
        )}
        <button
          className="google-ads-validation-button"
          type="button"
          disabled={launchDisabled}
          onClick={approvePausedCreate}
        >
          {approving ? 'Asking the owner to confirm…' : 'Launch turned off'}
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
            {!briefAccepted && (
              <p className="google-ads-actions-locked">Describe the ad and create the preview before launching.</p>
            )}
            {briefAccepted && !reviewAcknowledged && (
              <p className="google-ads-actions-locked">Read the preview and check the box before launching.</p>
            )}
            <p className="google-ads-status-note">
              {approvalReadiness.dispatch_enabled
                ? 'This website is ready to create the campaign turned off after the owner confirms.'
                : 'This website is not ready to create the campaign yet. Ask the owner before launching.'}
              {' '}
              That only describes this website. It does not prove the separate Google step is running.
              The owner should confirm that before launching. If that step is already running, Google
              may create the turned-off campaign right after the owner confirms. The ads stay off.
            </p>
          </>
        )}
        {approvalResult && (
          <p className="google-ads-validation-success" role="status">{launchResultCopy(status)}</p>
        )}
        {approvalError && <p className="google-ads-validation-error" role="alert">{approvalError}</p>}

        <ol className="google-ads-workflow" aria-label="Campaign progress">
          {status.workflow.map(step => (
            <li key={step.state} className={`is-${step.status}`}>
              <CircleCheck aria-hidden="true" size={18} />
              <span><strong>{STATE_LABELS[step.state]}</strong><small>{STATUS_LABELS[step.status]}</small></span>
            </li>
          ))}
        </ol>

        <details className="google-ads-review-detail">
          <summary>Technical details for the owner</summary>
          <p>You can skip this. These notes are for the owner.</p>
          <dl className="google-ads-status-details">
            <div><dt>Campaign name</dt><dd>{review.campaign_name}</dd></div>
            <div><dt>Campaign id</dt><dd>{status.deployment_key}</dd></div>
            <div><dt>Recorded events</dt><dd>{status.events.count}</dd></div>
            <div><dt>Ledger version</dt><dd>{status.version}</dd></div>
            <div><dt>Content hash</dt><dd>{status.contract_hash}</dd></div>
            <div><dt>Creation mode</dt><dd>Turned off · $0 spend</dd></div>
            {approvalReadiness && (
              <div>
                <dt>Storefront dispatch flag</dt>
                <dd>{approvalReadiness.dispatch_enabled ? 'Enabled' : 'Disabled'}</dd>
              </div>
            )}
            {connection && (
              <>
                <div><dt>Evidence digest</dt><dd>{connection.evidence_digest}</dd></div>
                <div><dt>Evidence revision</dt><dd>{connection.source_revision}</dd></div>
              </>
            )}
          </dl>
        </details>
      </article>
    </section>
  );
}
