import { expect, test } from 'playwright/test';

import { isLiveHost } from './target.mjs';

// Journeys stay on the candidate tag host. Cloud Run answers /, /inventory,
// /contact, and /appointments with 308 to the live site, so a browser that
// followed those redirects would be testing production. /staff is served by
// the candidate and returns the same SPA bundle. Later pages are opened with
// history.pushState, which the app already listens for, and their API reads
// stay on this origin.

const HOME_H1 = /Manufactured and Mobile Homes for Sale/;
const INVENTORY_H1 = /Home Inventory in/;
const GATEWAY_HTML = /502 Server Error|Please try again in 30 seconds/i;
const WRITE_PATH = /\/api\/(contact|appointments|leads|email\/send|admin\/(verify|email-code))/;
const liveHits = [];
const forbiddenWrites = [];

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

async function installGuards(context) {
  await context.addInitScript(() => {
    document.addEventListener('submit', (event) => event.preventDefault(), true);
  });
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = request.url();
    if (isLiveHost(hostOf(url))) {
      liveHits.push(`${request.method()} ${url}`);
      return route.abort('blockedbyclient');
    }
    const method = request.method().toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      const path = hostOf(url) ? new URL(url).pathname : url;
      if (WRITE_PATH.test(path)) forbiddenWrites.push(`${method} ${path}`);
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
}

async function openStaff(page) {
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await page.goto('/staff', { waitUntil: 'domcontentloaded' });
      const status = response?.status() ?? 0;
      const type = response?.headers()['content-type'] || '';
      if (status >= 500 && attempt < 3) {
        const body = type.includes('text/html') ? await response.text() : '';
        const gateway = GATEWAY_HTML.test(body) || type.includes('text/html');
        console.log(
          `gauntlet transient /staff status=${status} gateway_html=${gateway} attempt=${attempt}/3`,
        );
        await page.waitForTimeout(attempt === 1 ? 500 : 1500);
        continue;
      }
      expect(response?.ok(), 'GET /staff is served by the candidate').toBeTruthy();
      expect(isLiveHost(hostOf(page.url())), 'staff page left the candidate').toBeFalsy();
      await expect(page.getByRole('heading', { name: 'Staff sign-in' })).toBeVisible();
      return;
    } catch (error) {
      lastError = error;
      const message = String(error);
      if (message.includes('blockedbyclient') || message.includes('ERR_BLOCKED_BY_CLIENT')) {
        throw new Error(`Refusing to follow a request off the candidate. ${message}`);
      }
      if (attempt >= 3) break;
      console.log(`gauntlet transient /staff navigation attempt=${attempt}/3 ${message}`);
      await page.waitForTimeout(attempt === 1 ? 500 : 1500);
    }
  }
  throw lastError;
}

async function showPath(page, path) {
  await page.evaluate((nextPath) => {
    const url = new URL(nextPath, window.location.origin);
    if (url.origin !== window.location.origin) {
      throw new Error(`Refusing to leave the candidate for ${nextPath}`);
    }
    window.history.pushState({}, '', `${url.pathname}${url.search}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, path);
  await expect(page).toHaveURL(new RegExp(`${path === '/' ? '/$' : path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
  expect(isLiveHost(hostOf(page.url()))).toBeFalsy();
}

async function recoverInventory(page) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const failed = page.getByText('Unable to load inventory');
    if (!(await failed.isVisible().catch(() => false))) return;
    if (attempt === 3) return;
    console.log(`gauntlet transient inventory load attempt=${attempt}/3`);
    await page.getByRole('button', { name: 'Try Again' }).click();
    await page.waitForTimeout(attempt === 1 ? 500 : 1500);
  }
}

async function waitForCatalog(page) {
  const ready = page.getByRole('link', { name: /View Details/i }).first();
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      await ready.waitFor({ state: 'visible', timeout: 20_000 });
      return;
    } catch (error) {
      lastError = error;
      await recoverInventory(page);
      if (attempt === 3) break;
    }
  }
  throw lastError;
}

test.describe('candidate storefront journeys', () => {
  test.describe.configure({ mode: 'serial' });

  /** @type {import('playwright').BrowserContext} */
  let context;
  /** @type {import('playwright').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    await installGuards(context);
    page = await context.newPage();
    await openStaff(page);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  test('staff sign-in page loads and is not submitted', async () => {
    await showPath(page, '/staff');
    await expect(page.getByRole('heading', { name: 'Staff sign-in' })).toBeVisible();
    await expect(page.getByLabel('Work email')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Email me a sign-in link' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'You are signed in' })).toHaveCount(0);
  });

  test('home renders from the candidate bundle', async () => {
    await showPath(page, '/');
    await waitForCatalog(page);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(HOME_H1);
    await expect(page.getByRole('textbox', { name: 'Search homes' })).toBeVisible();
  });

  test('inventory browse filters the candidate catalog', async () => {
    await showPath(page, '/inventory');
    await waitForCatalog(page);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(INVENTORY_H1);
    const before = await page.getByRole('link', { name: /View Details/i }).count();
    expect(before).toBeGreaterThan(0);

    await page.getByRole('button', { name: 'Filters' }).click();
    await page.locator('#filter-beds').selectOption('3');
    await expect(page.locator('#filter-beds')).toHaveValue('3');
    await page.getByRole('textbox', { name: 'Search homes' }).fill('zzzz-gauntlet-no-such-home');
    await expect(page.getByText('No homes match your search')).toBeVisible();
    await page.getByRole('button', { name: 'Clear All Filters' }).click();
    await expect(page.getByRole('link', { name: /View Details/i }).first()).toBeVisible();
  });

  test('listing detail renders from the candidate catalog', async () => {
    await showPath(page, '/inventory');
    await waitForCatalog(page);
    const href = await page.getByRole('link', { name: /View Details/i }).first().getAttribute('href');
    expect(href, 'listing card exposes a detail path').toBeTruthy();
    const path = new URL(href, 'https://candidate.example').pathname;
    expect(path.startsWith('/homes/') || path.startsWith('/plan/') || path.startsWith('/inventory-detail/')).toBeTruthy();
    await showPath(page, path);
    const heading = page.getByRole('heading', { level: 1 });
    await expect(heading).toBeVisible();
    await expect(heading).not.toHaveText(HOME_H1);
    await expect(heading).not.toHaveText(INVENTORY_H1);
    await recoverInventory(page);
    await expect(page.getByText(/Beds:|Baths:|Square feet:|Stock #/).first()).toBeVisible();
  });

  test('contact page renders and is not submitted', async () => {
    await showPath(page, '/contact');
    for (const id of ['contact-name', 'contact-phone', 'contact-email', 'contact-message']) {
      await expect(page.locator(`#${id}`)).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'Send Message' })).toBeVisible();
    expect(forbiddenWrites.filter((entry) => entry.includes('/api/contact'))).toEqual([]);
  });

  test('appointment slots are read and not booked', async () => {
    await showPath(page, '/appointments');
    await expect(page.getByRole('heading', { name: 'Book an Appointment' })).toBeVisible();
    const day = page.getByRole('button', { name: /^\d{1,2}$/, disabled: false }).first();
    const slotsResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/appointments/slots' && response.request().method() === 'GET';
    });
    await day.click();
    const response = await slotsResponse;
    expect(isLiveHost(hostOf(response.url()))).toBeFalsy();
    expect(response.status(), 'slots read stays below 500').toBeLessThan(500);
    const body = await response.json();
    expect(Array.isArray(body.available_slots)).toBeTruthy();
    await expect(page.getByRole('button', { name: 'Review & Confirm' })).toHaveCount(0);
    expect(forbiddenWrites.filter((entry) => entry.includes('/api/appointments'))).toEqual([]);
  });

  test('no live-host requests and no lead, appointment, or email writes', async () => {
    expect(liveHits, liveHits.join('\n')).toEqual([]);
    expect(forbiddenWrites, forbiddenWrites.join('\n')).toEqual([]);
  });
});

test('public HTML redirects are not followed', async ({ request }) => {
  for (const path of ['/', '/inventory', '/contact', '/appointments']) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBe(308);
    const location = response.headers().location || '';
    expect(location.startsWith('https://www.texashomeoutlet.com'), location).toBeTruthy();
    expect(isLiveHost(hostOf(response.url()))).toBeFalsy();
  }
});
