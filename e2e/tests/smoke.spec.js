import { test, expect } from 'playwright/test';

// Register before navigation. Block application writes and background analytics.
test.beforeEach(async ({ context }) => {
  await context.route('**/*', route => {
    if (['GET', 'HEAD'].includes(route.request().method())) return route.continue();
    return route.abort('blockedbyclient');
  });
  await context.addInitScript(() => {
    document.addEventListener('submit', event => event.preventDefault(), true);
  });
});

async function visit(page, path) {
  const response = await page.goto(path, { waitUntil: 'domcontentloaded' });
  expect(response?.ok(), `GET ${path} succeeds`).toBeTruthy();
}

test('home renders', async ({ page }) => {
  await visit(page, '/');
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
});

test('inventory has a home card with a loaded image', async ({ page }) => {
  await visit(page, '/inventory');
  await expect(page.getByRole('link', { name: /View Details/i }).first()).toBeVisible();
  const image = page.locator('img[loading="lazy"]').first();
  await image.scrollIntoViewIfNeeded();
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
});

test('a published plan detail renders', async ({ page, request }) => {
  const sitemap = await request.get('/sitemap.xml');
  expect(sitemap.ok()).toBeTruthy();
  const locations = [...(await sitemap.text()).matchAll(/<loc>([^<]+)<\/loc>/g)];
  const plan = locations.map(match => new URL(match[1]).pathname).find(path => path.startsWith('/plan/'));
  expect(plan, 'Sitemap publishes at least one plan').toBeTruthy();
  // Use only the path so the test stays on BASE_URL rather than the canonical host.
  await visit(page, plan);
  await expect(page.getByRole('heading', { level: 2 }).last()).toBeVisible();
  await expect(page.getByRole('button', { name: /Photos \(/ }).first()).toBeVisible();
});

test('contact form shell renders without submission', async ({ page }) => {
  await visit(page, '/contact');
  for (const id of ['contact-name', 'contact-phone', 'contact-email', 'contact-message']) {
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
});

test('appointments booking shell renders without submission', async ({ page }) => {
  await visit(page, '/appointments');
  await expect(page.getByRole('heading', { name: 'Book an Appointment' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next month' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^\d{1,2}$/ }).first()).toBeVisible();
});

test('healthz returns JSON with a version', async ({ request }) => {
  const response = await request.get('/healthz/');
  expect(response.ok()).toBeTruthy();
  expect(response.headers()['content-type']).toContain('application/json');
  const body = await response.json();
  expect(body.version).toEqual(expect.any(String));
  expect(body.version.trim().length).toBeGreaterThan(0);
});

for (const [path, heading] of [['/privacy', /privacy/i], ['/terms', /terms/i]]) {
  test(`${path} legal page renders`, async ({ page }) => {
    await visit(page, path);
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible();
  });
}
