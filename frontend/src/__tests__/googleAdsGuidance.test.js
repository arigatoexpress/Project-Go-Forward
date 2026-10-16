import { describe, expect, it } from 'vitest';

import {
  EXAMPLE_BRIEF,
  GATE_GUIDANCE,
  OWNER_ONLY_STEPS,
  STAFF_BRIEF_STORAGE_KEY,
  adPreview,
  briefProblem,
  budgetPlain,
  launchResultCopy,
  locationPlain,
  plainRemediation,
  readStaffBrief,
  terminalRemediation,
  writeStaffBrief,
} from '../components/ad-studio/googleAdsGuidance';

describe('Google Ads staff guidance', () => {
  it('keeps the staff description in local storage and ignores storage failures', () => {
    const storage = {
      value: null,
      getItem() { return this.value; },
      setItem(_key, value) { this.value = value; },
      removeItem() { this.value = null; },
    };
    writeStaffBrief('People near Huffman should see prices.', storage);
    expect(storage.value).toBe('People near Huffman should see prices.');
    expect(readStaffBrief(storage)).toBe('People near Huffman should see prices.');
    writeStaffBrief('', storage);
    expect(readStaffBrief(storage)).toBe('');

    const blocked = {
      getItem() { throw new Error('blocked'); },
      setItem() { throw new Error('blocked'); },
      removeItem() { throw new Error('blocked'); },
    };
    expect(readStaffBrief(blocked)).toBe('');
    expect(() => writeStaffBrief('still here', blocked)).not.toThrow();
    expect(STAFF_BRIEF_STORAGE_KEY).toBe('tho-google-ads-staff-brief');
  });

  it('asks for a plain-language description before a blank launch', () => {
    expect(briefProblem('')).toMatch(/who should see this ad/i);
    expect(briefProblem('too short')).toMatch(/one or two sentences/i);
    expect(briefProblem(EXAMPLE_BRIEF)).toBe('');
  });

  it('translates owner setup gaps and never echoes raw job jargon', () => {
    expect(plainRemediation([
      'Complete offline server validation.',
      'Run the read-only Google Ads account-access and USD-currency evidence job.',
      'Verify current cloud/IAM readiness and enable owner approval config.',
      'Unexpected control-plane detail',
    ])).toBe([
      'Check the campaign before you launch it.',
      'The owner still needs to link the Google Ads account and confirm it uses US dollars.',
      'The owner still needs to finish ads setup: account permissions, API credentials, and the turned-off campaign job.',
      'The owner still has a setup step to finish before this campaign can be launched turned off.',
    ].join(' '));
    expect(plainRemediation([])).toBe('');
  });

  it('names the four owner-only steps and keeps spend off in the result copy', () => {
    expect(OWNER_ONLY_STEPS.map(step => step.title)).toEqual([
      'Link the Google Ads account',
      'Set up billing in Google Ads',
      'Turn on conversion tracking',
      'Add the API credentials',
    ]);
    expect(GATE_GUIDANCE.iam_verified.missing).toMatch(/permissions/i);
    expect(budgetPlain({
      average_daily_usd: 20,
      max_single_day_charge_usd: 40,
      monthly_charge_limit_usd: 608,
      max_cpc_usd: 5,
    })).toMatch(/leaves them off/);
    expect(locationPlain({
      radius_miles: 50,
      center: { address: '10685 FM 1960 East', city: 'Huffman', state: 'TX' },
    })).toMatch(/Google Search only/);
    expect(launchResultCopy({
      state: 'PAUSED_CREATED',
      paused_create: { outbox_state: 'DISPATCHED' },
    })).toMatch(/turned off/);
    expect(terminalRemediation({
      state: 'PAUSED_CREATE_APPROVED',
      paused_create: { outbox_state: 'FAILED' },
    })).toMatch(/no money was spent/i);
  });

  it('builds a search-ad preview from the reviewed group', () => {
    const preview = adPreview({
      name: 'Local Inventory',
      responsive_search_ad: {
        headlines: ['Texas Home Outlet', 'Browse Homes With Prices', 'Explore Home Models'],
        descriptions: ['Browse homes near Huffman.', 'Request a tour.'],
        path1: 'homes',
        path2: 'huffman',
        final_url: 'https://www.texashomeoutlet.com/inventory',
      },
      keywords: [{ text: 'manufactured homes huffman tx' }],
    });

    expect(preview.headline).toBe('Texas Home Outlet | Browse Homes With Prices | Explore Home Models');
    expect(preview.displayUrl).toBe('www.texashomeoutlet.com/homes/huffman');
    expect(preview.finalUrl).toBe('https://www.texashomeoutlet.com/inventory');
    expect(preview.keywords).toEqual(['manufactured homes huffman tx']);
  });
});