import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InventoryBrowse from '../pages/InventoryBrowse';
import Financing from '../pages/Financing';
import Contact from '../pages/Contact';
import About from '../pages/About';
import Warranty from '../pages/Warranty';
import Delivery from '../pages/Delivery';
import FAQ from '../pages/FAQ';
import LegalPage from '../pages/LegalPage';
import {
  BUSINESS_FULL_ADDRESS,
  BUSINESS_LEGAL_NAME,
  BUSINESS_LICENSE,
  BUSINESS_PHONE,
} from '../constants';
import { DELIVERY_LINE, FINANCING_LINE, WARRANTY_LINE } from '../seoCopy';

// Claims the owner has not confirmed. These must not render on public pages.
const FORBIDDEN = [
  /TODO\(owner\)/i,
  /\[THO:/i,
  /\[\[OWNER/i,
  /21st Mortgage/i,
  /Vanderbilt/i,
  /\bTriad\b/i,
  /free delivery/i,
  /guaranteed approval/i,
  /30-day/i,
  /1-year/i,
  /1 year from/i,
  /30 days from/i,
  /family-focused/i,
  /meets or exceeds/i,
  /within weeks/i,
  /delivers manufactured/i,
  /Inventory changes daily/i,
  /281-657-7366/,
  /\(281\) 555-0199/,
  /Local Home Services/i,
  /award-winning/i,
  /5-star/i,
  /\bFHA\b/,
  /\bUSDA\b/,
  /\bChattel\b/i,
  /dream home/i,
  /\bTBD\b/,
  /Apply for financing/i,
  /modular home retailer/i,
  /budget-friendly/i,
  /lender partners/i,
];

const home = {
  id: '44490',
  model_name: 'PRE-OWNED / Big Blue',
  manufacturer: 'Pre-Owned',
  status: 'Pre-Owned',
  inventory_kind: 'pre_owned',
  display_price: 'Call for Price',
  price_value: 0,
  image_url: 'https://lot.example/44490.jpg',
  real_photos: ['https://lot.example/44490.jpg'],
  specs: { beds: 3, baths: 2, sq_ft: 1152 },
};

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

function assertClean(label) {
  const text = document.body.textContent || '';
  for (const pattern of FORBIDDEN) {
    expect(text, `${label} still shows ${pattern}`).not.toMatch(pattern);
  }
}

function assertNoNewEmDash(label) {
  expect(document.body.textContent || '', `${label} has an em dash`).not.toMatch(/\u2014/);
}

describe('public pages omit unverified claims', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url) === '/api/marketing/inventory-context') {
        return response({ success: true, homes: [home] });
      }
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  it('keeps Call for Price on the home inventory and drops placeholder claims', async () => {
    window.history.replaceState({}, '', '/');
    render(<InventoryBrowse pathname="/" />);
    expect(await screen.findAllByText('Call for Price')).not.toHaveLength(0);
    expect(screen.getAllByRole('link', { name: BUSINESS_PHONE }).length).toBeGreaterThan(0);
    expect(screen.getByText('Confirm availability with our team')).toBeInTheDocument();
    assertClean('home');
  });

  it('keeps Call for Price on the inventory catalog', async () => {
    window.history.replaceState({}, '', '/inventory');
    render(<InventoryBrowse pathname="/inventory" />);
    expect(await screen.findAllByText('Call for Price')).not.toHaveLength(0);
    assertClean('inventory');
  });

  it('keeps Call for Price on a home detail page', async () => {
    window.history.replaceState({}, '', '/homes/44490-pre-owned-big-blue');
    render(<InventoryBrowse pathname="/homes/44490-pre-owned-big-blue" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'PRE-OWNED / Big Blue' })).toBeInTheDocument();
    expect(screen.getAllByText('Call for Price').length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Ask about financing/i }).length).toBeGreaterThan(0);
    assertClean('home detail');
  });

  it('uses the neutral financing line', () => {
    render(<Financing onBack={() => {}} />);
    expect(screen.getByText(FINANCING_LINE)).toBeInTheDocument();
    assertClean('financing');
    assertNoNewEmDash('financing');
  });

  it('keeps the recorded contact facts', () => {
    render(<Contact onBack={() => {}} />);
    expect(screen.getByText('Call, visit the showroom, or send a message.')).toBeInTheDocument();
    expect(screen.getByText(BUSINESS_FULL_ADDRESS)).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: BUSINESS_PHONE }).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'sales@texashomeoutlet.com' })).toBeInTheDocument();
    assertClean('contact');
    assertNoNewEmDash('contact');
  });

  it('keeps the recorded license, legal name, and showroom facts on About', () => {
    render(<About onBack={() => {}} />);
    expect(screen.getByText(new RegExp(`License #${BUSINESS_LICENSE}`))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(BUSINESS_LEGAL_NAME))).toBeInTheDocument();
    expect(screen.getAllByText(BUSINESS_FULL_ADDRESS).length).toBeGreaterThan(0);
    expect(screen.getByText(FINANCING_LINE)).toBeInTheDocument();
    expect(screen.getByText(DELIVERY_LINE)).toBeInTheDocument();
    expect(screen.getByText(WARRANTY_LINE)).toBeInTheDocument();
    expect(screen.getByText(/Call for Price/)).toBeInTheDocument();
    assertClean('about');
    assertNoNewEmDash('about');
  });

  it('uses the neutral warranty and delivery lines', () => {
    render(<Warranty onBack={() => {}} />);
    expect(screen.getAllByText(new RegExp(WARRANTY_LINE)).length).toBeGreaterThan(0);
    expect(screen.getByText(/Safety First/i)).toBeInTheDocument();
    assertClean('warranty');
    assertNoNewEmDash('warranty');
    cleanup();

    render(<Delivery onBack={() => {}} />);
    expect(screen.getAllByText(new RegExp(DELIVERY_LINE)).length).toBeGreaterThan(0);
    assertClean('delivery');
    assertNoNewEmDash('delivery');
  });

  it('keeps FAQ answers aligned with the neutral lines', () => {
    render(<FAQ onBack={() => {}} />);
    expect(screen.getByText(FINANCING_LINE)).toBeInTheDocument();
    expect(screen.getByText(DELIVERY_LINE)).toBeInTheDocument();
    expect(screen.getByText(WARRANTY_LINE)).toBeInTheDocument();
    expect(screen.getByText(/Call for Price/)).toBeInTheDocument();
    assertClean('faq');
    assertNoNewEmDash('faq');
  });

  it('keeps recorded identity facts on the legal pages', () => {
    render(<LegalPage page="privacy" />);
    expect(screen.getByText(/manufactured and mobile home retailer/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(BUSINESS_LEGAL_NAME))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(BUSINESS_PHONE.replace(/[()]/g, '\\$&')))).toBeInTheDocument();
    assertClean('privacy');
    assertNoNewEmDash('privacy');
    cleanup();

    render(<LegalPage page="terms" />);
    expect(screen.getByText(/manufactured and mobile home retailer/)).toBeInTheDocument();
    expect(screen.getByText(/not loan approvals or guaranteed rates or terms/)).toBeInTheDocument();
    assertClean('terms');
    assertNoNewEmDash('terms');
  });
});
