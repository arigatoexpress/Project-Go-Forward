import { test, expect } from 'playwright/test';
import {
  FIXTURE_HOME,
  FIXTURE_PIN,
  STUDIO_HOME,
  internalDraftStatus,
} from '../../frontend/src/__tests__/fixtures/adminAdsClickPath.js';

const LIVE_WRITE = /\/api\/(admin\/(verify|email-code|google-ads|logout|passkey)|inventory|marketing\/(generate|schedule))/;

function isLoopback(baseURL) {
  try {
    return ['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseURL).hostname);
  } catch {
    return false;
  }
}

// Mocked sign-in runs only when this is set and BASE_URL is loopback.
// A plain local smoke run stays read-only and does not post the fixture PIN.
function useLocalMocks(baseURL) {
  return process.env.CLICKPATH_MOCKS === '1' && isLoopback(baseURL);
}

function json(route, data, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  });
}

async function installLocalMocks(page) {
  let signedIn = false;
  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method().toUpperCase();
    if (!url.pathname.startsWith('/api/')) {
      if (method === 'GET' || method === 'HEAD') return route.continue();
      return route.abort('blockedbyclient');
    }
    const path = url.pathname;
    if (path === '/api/admin/verify' && method === 'POST') {
      const body = request.postDataJSON() || {};
      if (body.pin === FIXTURE_PIN) {
        signedIn = true;
        return json(route, { success: true });
      }
      return json(route, { success: false, error: 'That PIN did not work.' });
    }
    if (path === '/api/admin/check') return json(route, { valid: signedIn });
    if (path === '/api/admin/sign-in/options') {
      return json(route, { email_ready: true, passkey_sign_in: false });
    }
    if (path === '/api/admin/passkey/status') {
      return json(route, { enabled: false, store_ready: false, has_keys: false });
    }
    if (path === '/api/admin/google-ads/draft' && method === 'POST') {
      return json(route, internalDraftStatus());
    }
    if (path.startsWith('/api/inventory/') && path.endsWith('/photos')) {
      return json(route, { photos: [] });
    }
    if (path === '/api/inventory' || path.startsWith('/api/inventory/')) {
      if (method !== 'GET') return json(route, { success: false }, 405);
      return json(route, { success: true, inventory: [FIXTURE_HOME] });
    }
    if (path === '/api/marketing/inventory-context') {
      return json(route, { success: true, homes: [STUDIO_HOME] });
    }
    if (path === '/api/marketing/voiceover-voices') {
      return json(route, { success: true, voices: [] });
    }
    if (path === '/api/marketing/gcp-readiness') {
      return json(route, { success: true, ready: false });
    }
    if (method !== 'GET' && method !== 'HEAD') return route.abort('blockedbyclient');
    return json(route, { success: true });
  });
}

async function openStaffTool(page, name) {
  await page.getByRole('button', { name: 'More', exact: true }).click();
  await page.getByTestId('staff-more-menu').getByRole('menuitem', { name, exact: true }).click();
}

test.use({ viewport: { width: 1280, height: 900 } });

test('admin, listing, Ads, and AI Ads Studio click path', async ({ page, baseURL }) => {
  const local = useLocalMocks(baseURL);
  const blockedWrites = [];
  if (local) {
    await installLocalMocks(page);
  } else {
    await page.route('**/*', (route) => {
      const request = route.request();
      if (['GET', 'HEAD'].includes(request.method())) return route.continue();
      blockedWrites.push(`${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    });
    await page.addInitScript(() => {
      document.addEventListener('submit', (event) => event.preventDefault(), true);
    });
  }

  const results = [];
  async function step(name, mode, fn) {
    if (mode === 'local' && !local) {
      results.push({ name, result: 'not run', detail: 'Needs a fixture sign-in. This run does not submit credentials or forms.' });
      return;
    }
    if (mode === 'live' && local) {
      results.push({ name, result: 'not run', detail: 'Signed-out check. The mocked run is already signed in.' });
      return;
    }
    try {
      await fn();
      results.push({ name, result: 'pass' });
    } catch (error) {
      results.push({ name, result: 'fail', detail: error?.message || String(error) });
    }
  }

  await step('Staff sign-in page is visible', 'both', async () => {
    const response = await page.goto('/staff', { waitUntil: 'domcontentloaded' });
    expect(response?.ok(), 'GET /staff succeeds').toBeTruthy();
    await expect(page.getByRole('heading', { name: 'Staff sign-in' })).toBeVisible();
    await expect(page.getByLabel('Work email')).toBeVisible();
    if (!local) await expect(page.getByLabel('Work email')).toHaveValue('');
  });

  await step('Fixture backup PIN signs in', 'local', async () => {
    await page.getByRole('button', { name: 'Use backup PIN' }).click();
    await page.getByLabel('Backup PIN').fill(FIXTURE_PIN);
    await page.getByRole('button', { name: 'Unlock' }).click();
    await expect(page.getByRole('heading', { name: 'Staff Home' })).toBeVisible();
    await expect(page).toHaveURL(/\/staff-home$/);
  });

  await step('Listing admin shows the fixture home', 'local', async () => {
    await page.getByRole('button', { name: /Add or change a home/ }).click();
    await expect(page.getByRole('heading', { name: /Homes on the website/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Clickpath Single' })).toBeVisible();
    await expect(page).toHaveURL(/\/manage-inventory$/);
    await page.getByRole('button', { name: 'Change this home' }).click();
    await expect(page.getByRole('heading', { name: 'Change this home' })).toBeVisible();
    await expect(page.getByLabel('Home name')).toHaveValue('Clickpath Single');
    await page.getByRole('button', { name: 'Done' }).click();
    await expect(page.getByLabel('Home name')).toHaveCount(0);
  });

  await step('Signed-out listing admin stays locked', 'live', async () => {
    const response = await page.goto('/manage-inventory', { waitUntil: 'domcontentloaded' });
    expect(response?.ok(), 'GET /manage-inventory succeeds').toBeTruthy();
    await expect(page.getByRole('heading', { name: 'Staff sign-in' })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Homes on the website/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Add a home' })).toHaveCount(0);
  });

  await step('Ads shows the create step and does not launch', 'local', async () => {
    await openStaffTool(page, 'Google Ads');
    await expect(page.getByRole('heading', { name: '1. Create' })).toBeVisible();
    await expect(page.getByLabel('Who should see this ad?')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Google Ads', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(page).toHaveURL(/\/ad-studio$/);
    await expect(page.getByRole('button', { name: /turn ads on|publish/i })).toHaveCount(0);
  });

  await step('Signed-out Ads stays locked', 'live', async () => {
    const response = await page.goto('/ad-studio', { waitUntil: 'domcontentloaded' });
    expect(response?.ok(), 'GET /ad-studio succeeds').toBeTruthy();
    await expect(page.getByRole('heading', { name: /Staff sign-in|Ad Studio is for staff/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: '1. Create' })).toHaveCount(0);
    await expect(page.getByText("Tex's Ad Studio")).toHaveCount(0);
  });

  await step('AI Ads Studio opens Create Ad and a mocked home', 'local', async () => {
    await openStaffTool(page, 'Ad Studio');
    await expect(page).toHaveURL(/\/studio$/);
    await expect(page.getByText("Tex's Ad Studio")).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create Ad', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('button', { name: 'Google Ads', exact: true })).not.toHaveAttribute('aria-current', 'page');
    await page.getByRole('button', { name: 'Choose a home' }).click();
    await expect(page.getByRole('heading', { name: 'Choose a home' })).toBeVisible();
    await page.getByRole('button', { name: /Clickpath Single/ }).click();
    await expect(page.getByLabel('Home name')).toHaveValue('Clickpath Single');
    await expect(page.getByLabel('Price')).toHaveValue('');
  });

  await step('Signed-out AI Ads Studio stays locked', 'live', async () => {
    const response = await page.goto('/studio', { waitUntil: 'domcontentloaded' });
    expect(response?.ok(), 'GET /studio succeeds').toBeTruthy();
    await expect(page.getByRole('heading', { name: /Staff sign-in|Ad Studio is for staff/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Choose a home' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Generate Script' })).toHaveCount(0);
    await expect(page.getByText("Tex's Ad Studio")).toHaveCount(0);
  });

  if (!local) {
    const leaked = blockedWrites.filter((entry) => LIVE_WRITE.test(entry));
    results.push({
      name: 'Live requests stayed read-only',
      result: leaked.length === 0 ? 'pass' : 'fail',
      detail: leaked.length === 0
        ? `Blocked ${blockedWrites.length} non-GET request(s): ${blockedWrites.join(' | ') || 'none'}. None matched admin, inventory, or ad write paths.`
        : leaked.join('; '),
    });
  }

  console.log(`CLICKPATH_RESULTS ${JSON.stringify({ target: local ? 'local' : 'live', baseURL, results })}`);
  const failed = results.filter((item) => item.result === 'fail');
  expect(failed, JSON.stringify(failed, null, 2)).toEqual([]);
});
