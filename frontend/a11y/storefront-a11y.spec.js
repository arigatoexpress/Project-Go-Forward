import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from 'playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Representative public inventory. Prices stay "Call for Price" so the scan
// does not invent a dollar amount. Photos are same-origin so the listing
// renders an image with alt text.
const HOMES = [
  {
    id: '44001',
    stock_number: '44001',
    model_name: 'Cedar Ridge',
    manufacturer: 'Sample Manufacturer',
    status: 'Available',
    inventory_kind: 'available_now',
    is_orderable: false,
    display_price: 'Call for Price',
    image_url: '/og-card.png',
    real_photos: ['/og-card.png'],
    gallery_images: [],
    specs: { beds: 3, baths: 2, sq_ft: 1456, dimensions: '28x56' },
  },
  {
    id: 'floorplan-1001',
    legacy_plan_id: '1001',
    model_name: 'Oak Plan',
    manufacturer: 'Sample Manufacturer',
    status: 'Orderable',
    inventory_kind: 'orderable_floorplan',
    is_orderable: true,
    display_price: 'Call for Price',
    detail_url: '/plan/1001/oak/plan/',
    image_url: '',
    real_photos: [],
    gallery_images: [],
    specs: { beds: 3, baths: 2 },
  },
];

const PAGES = [
  { name: 'home', path: '/', ready: 'Manufactured and Mobile Homes for Sale in Huffman, TX' },
  { name: 'inventory', path: '/inventory', ready: 'Home Inventory in Huffman, TX' },
  { name: 'listing', path: '/homes/44001-cedar-ridge', ready: 'Cedar Ridge' },
  { name: 'contact', path: '/contact', ready: 'Contact Us' },
  { name: 'staff-sign-in', path: '/staff', ready: 'Staff sign-in' },
];

function seriousViolations(results) {
  return results.violations
    .filter((violation) => violation.impact === 'critical' || violation.impact === 'serious')
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      nodes: violation.nodes.map((node) => ({
        target: node.target,
        html: String(node.html || '').slice(0, 240),
        failureSummary: node.failureSummary,
      })),
    }));
}

function writeReport(pageName, projectName, results) {
  const dir = process.env.AXE_REPORT_DIR;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  const summary = results.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    nodes: violation.nodes.length,
    targets: violation.nodes.slice(0, 12).map((node) => node.target),
  }));
  fs.writeFileSync(
    path.join(dir, `${pageName}-${projectName}.json`),
    JSON.stringify(summary, null, 2),
  );
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.addInitScript(() => {
    localStorage.setItem('tho_dark_mode', 'false');
  });
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    let body = { success: true };
    if (url.includes('/api/marketing/inventory-context')) body = { success: true, homes: HOMES };
    else if (url.includes('/api/admin/check')) body = { valid: false };
    else if (url.includes('/api/admin/sign-in/options')) {
      body = { passkey_sign_in: false, email_ready: true };
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
});

for (const pageInfo of PAGES) {
  test(`${pageInfo.name} has no critical or serious axe violations`, async ({ page }, testInfo) => {
    await page.goto(pageInfo.path, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: pageInfo.ready }).first()).toBeVisible();
    if (pageInfo.name === 'listing') {
      await expect(page.getByRole('link', { name: 'Browse all homes' })).toBeVisible();
    }
    if (pageInfo.name === 'contact') {
      await expect(page.locator('#contact-name')).toBeVisible();
    }
    if (pageInfo.name === 'staff-sign-in') {
      await expect(page.getByRole('textbox', { name: 'Work email' })).toBeVisible();
    }
    await page.evaluate(() => document.fonts?.ready);

    const results = await new AxeBuilder({ page }).analyze();
    const serious = seriousViolations(results);
    writeReport(pageInfo.name, testInfo.project.name, results);
    expect(serious, `${pageInfo.name} (${testInfo.project.name})`).toEqual([]);
  });
}
