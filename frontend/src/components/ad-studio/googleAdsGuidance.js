/** Plain-language copy for the Google Ads staff flow. No provider calls. */

export const EXAMPLE_BRIEF =
  'People within 50 miles of our Huffman showroom who are searching for a manufactured home should see our prices and be able to book a tour.';

export const STATE_LABELS = {
  INTERNAL_DRAFT: 'Write the campaign',
  SERVER_VALIDATED: 'Campaign checked',
  PAUSED_CREATE_APPROVED: 'Launch approved',
  PAUSED_CREATED: 'Created and turned off',
};

export const STATUS_LABELS = {
  current: 'You are here',
  not_started: 'Not yet',
  complete: 'Done',
};

export const OWNER_ONLY_STEPS = [
  {
    title: 'Link the Google Ads account',
    detail: 'The owner connects the Texas Home Outlet Google Ads account. This screen cannot link accounts.',
  },
  {
    title: 'Set up billing in Google Ads',
    detail: 'The owner confirms billing and that the account uses US dollars. This screen never changes billing and never spends money.',
  },
  {
    title: 'Count tour bookings',
    detail: 'The owner connects tour bookings and lead form fills so Google can tell a useful visit from a click. This screen cannot turn ads on.',
  },
  {
    title: 'Finish the Google connection',
    detail: 'The owner connects this website to the Google Ads account. Do not paste codes, tokens, or passwords on this screen.',
  },
];

export const GATE_GUIDANCE = {
  feature_enabled: {
    ok: 'The owner has allowed a turned-off campaign to be created.',
    missing: 'The owner has not allowed a turned-off campaign to be created yet.',
  },
  cloud_readiness_verified: {
    ok: 'Google Cloud setup for ads is finished.',
    missing: 'The owner still needs to finish the Google Cloud setup for ads.',
  },
  iam_verified: {
    ok: 'Ads account permissions are in place.',
    missing: 'The owner still needs to grant the ads account permissions.',
  },
  revision_bound: {
    ok: 'This website version is cleared for ads.',
    missing: 'This website version is not the one cleared to create the campaign.',
  },
  dispatcher_configured: {
    ok: 'The step that creates the turned-off campaign is set up.',
    missing: 'The owner still needs to set up the step that creates the turned-off campaign.',
  },
};

const REMEDIATION_PLAIN = {
  'Complete offline server validation.': 'Check the campaign before you launch it.',
  'Run the read-only Google Ads account-access and USD-currency evidence job.':
    'The owner still needs to link the Google Ads account and confirm it uses US dollars.',
  'Verify current cloud/IAM readiness and enable owner approval config.':
    'The owner still needs to finish ads setup: account permissions, the Google connection, and the turned-off campaign step.',
};

const SAFE_REMEDIATION_FALLBACK =
  'The owner still has a setup step to finish before this campaign can be launched turned off.';

export const STAFF_BRIEF_STORAGE_KEY = 'tho-google-ads-staff-brief';

export const STAFF_CAMPAIGN_NAME = 'Huffman search ads';

const OUTBOX_PLAIN = {
  PENDING: 'waiting to be created in Google Ads',
  DISPATCHING: 'being created in Google Ads now',
  DISPATCHED: 'created in Google Ads and turned off',
  FAILED: 'not created',
};

export function outboxPlain(state) {
  return OUTBOX_PLAIN[String(state || '').toUpperCase()] || 'saved on this website';
}

export function progressPlain(status) {
  if (status?.state === 'SERVER_VALIDATED') {
    return 'The ad is checked. It is still turned off, and nothing has been spent.';
  }
  if (status?.state === 'PAUSED_CREATE_APPROVED') {
    return 'Approved, and still turned off.';
  }
  if (status?.state === 'PAUSED_CREATED') {
    return 'In Google Ads, and still turned off.';
  }
  return 'Start here. Nothing has been sent to Google.';
}

export function checkButtonLabel(canValidate) {
  return canValidate ? 'Check this ad' : 'Use this description';
}

export function readStaffBrief(storage = globalThis.localStorage) {
  try {
    return String(storage?.getItem(STAFF_BRIEF_STORAGE_KEY) || '');
  } catch {
    return '';
  }
}

export function writeStaffBrief(text, storage = globalThis.localStorage) {
  try {
    if (!storage) return;
    const value = String(text ?? '');
    if (value) storage.setItem(STAFF_BRIEF_STORAGE_KEY, value);
    else storage.removeItem(STAFF_BRIEF_STORAGE_KEY);
  } catch {
    // Private browsing can reject storage. The description still works for this visit.
  }
}

export function briefProblem(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed) {
    return 'Describe who should see this ad and what you want them to do.';
  }
  if (trimmed.length < 20) {
    return 'Add a little more detail. One or two sentences is enough.';
  }
  return '';
}

export function plainRemediation(items) {
  if (!Array.isArray(items) || items.length === 0) return '';
  return items
    .map(item => REMEDIATION_PLAIN[item] || SAFE_REMEDIATION_FALLBACK)
    .join(' ');
}

export function budgetPlain(budget) {
  return `About $${budget.average_daily_usd} a day. Google can charge up to $${budget.max_single_day_charge_usd} in one day and $${budget.monthly_charge_limit_usd} in a month, and only after the owner turns the ads on. This launch leaves them off. One click costs at most $${budget.max_cpc_usd}.`;
}

export function locationPlain(geo) {
  const center = geo.center;
  return `Google Search only, for people physically within ${geo.radius_miles} miles of ${center.address}, ${center.city}, ${center.state}. It does not target ZIP codes, and it does not show on other Google sites or display ads.`;
}

export function adPreview(group) {
  const ad = group.responsive_search_ad;
  return {
    name: group.name,
    headline: ad.headlines.slice(0, 3).join(' | '),
    headlines: [...ad.headlines],
    description: ad.descriptions[0],
    descriptions: [...ad.descriptions],
    displayUrl: `www.texashomeoutlet.com/${ad.path1}/${ad.path2}`,
    finalUrl: ad.final_url,
    keywords: group.keywords.map(keyword => keyword.text),
  };
}

export function launchResultCopy(status) {
  if (status?.state === 'PAUSED_CREATED') {
    return 'The campaign is in Google Ads and turned off. It is not spending money.';
  }
  if (status?.state === 'PAUSED_CREATE_APPROVED') {
    return 'The owner confirmed it. The campaign stays turned off and no money is being spent.';
  }
  return 'The launch was recorded. Refresh the page before doing anything else. No money was spent.';
}

export function terminalRemediation(status) {
  if (status?.state === 'PAUSED_CREATED') {
    return 'The campaign is in Google Ads and turned off. It is not spending money. The owner still has to finish billing and lead counting before ads can run. This screen cannot turn ads on.';
  }
  if (status?.paused_create?.outbox_state === 'FAILED') {
    return 'We could not finish creating the turned-off campaign. It stays off and no money was spent. Ask the owner to review the ads setup. Do not try again from this screen.';
  }
  return `The turned-off campaign is approved and ${outboxPlain(status?.paused_create?.outbox_state)}. No money is being spent. This screen cannot turn ads on.`;
}
