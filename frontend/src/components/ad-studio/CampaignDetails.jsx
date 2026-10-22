import { useState } from 'react';

import {
  adPreview,
  budgetFieldRows,
  campaignStartedAt,
  campaignStatusPlain,
  formatStaffDate,
  groupCopyText,
} from './googleAdsGuidance';

const COPY_FAILED = 'The ad text was not copied. Select the headlines and descriptions and copy them yourself.';

export function CopyAdTextButton({ group }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  async function copyText() {
    try {
      await navigator.clipboard.writeText(groupCopyText(group));
      setFailed(false);
      setCopied(true);
      globalThis.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setFailed(true);
    }
  }

  return (
    <div className="google-ads-copy-row">
      <button
        type="button"
        className="google-ads-text-button"
        aria-label={`Copy headlines and descriptions for ${group.name}`}
        onClick={copyText}
      >
        {copied ? 'Copied' : 'Copy headlines and descriptions'}
      </button>
      {copied && <span role="status">Copied</span>}
      {failed && <p className="google-ads-validation-error" role="alert">{COPY_FAILED}</p>}
    </div>
  );
}

export default function CampaignDetails({ status }) {
  const review = status?.campaign_review;
  if (!review) return null;
  const updated = formatStaffDate(status.updated_at);
  const started = formatStaffDate(campaignStartedAt(status));
  const rows = [
    ['Name', review.campaign_name],
    ['Status', campaignStatusPlain(status)],
    updated ? ['Last updated', updated] : null,
    started ? ['Started', started] : null,
    ...budgetFieldRows(status.budget),
  ].filter(Boolean);

  return (
    <article className="google-ads-status-panel" aria-labelledby="campaign-details-heading">
      <h2 id="campaign-details-heading">Campaign details</h2>
      <p className="google-ads-status-note">
        Status, dates, budget, and ad text saved for this campaign.
        This screen cannot change the budget or turn ads on.
      </p>
      <dl className="google-ads-status-details google-ads-campaign-details">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="google-ads-copy-blocks">
        {(Array.isArray(review.ad_groups) ? review.ad_groups : []).map(group => {
          const preview = adPreview(group);
          return (
            <div key={`${group.name}-copy`} className="google-ads-copy-block">
              <h3>{group.name}</h3>
              <p className="google-ads-preview-meta">Headlines</p>
              <ul>
                {preview.headlines.map((line, index) => <li key={`${line}-${index}`}>{line}</li>)}
              </ul>
              <p className="google-ads-preview-meta">Descriptions</p>
              <ul>
                {preview.descriptions.map((line, index) => <li key={`${line}-${index}`}>{line}</li>)}
              </ul>
              <CopyAdTextButton group={group} />
            </div>
          );
        })}
      </div>
    </article>
  );
}
