import { expect, test } from 'playwright/test';

const STAFF_HOME = {
  id: '44001',
  model_name: 'Cedar Ridge',
  status: 'AVAILABLE',
  stock_number: '44001',
  real_photos: [],
  gallery_images: [],
};

const PUBLIC_HOMES = [
  {
    id: '44001',
    model_name: 'Cedar Ridge',
    status: 'Available',
    inventory_kind: 'available_now',
    display_price: 'Call for Price',
    image_url: '/og-card.png',
    real_photos: ['/og-card.png'],
    gallery_images: ['/og-card.png'],
    specs: { beds: 3, baths: 2, sq_ft: 1200 },
  },
  {
    id: '44002',
    model_name: 'Oak Plan',
    status: 'Available',
    inventory_kind: 'available_now',
    display_price: 'Call for Price',
    image_url: '/og-card.png',
    real_photos: ['/og-card.png'],
    gallery_images: ['/og-card.png'],
    matterport_id: 'JbALitnto3g',
    specs: { beds: 3, baths: 2, sq_ft: 1400 },
  },
];

function fulfill(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installApi(page, { staff = false, homes = PUBLIC_HOMES } = {}) {
  let savedId = '';
  let lastPost = '';
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = request.url();
    const method = request.method();
    if (url.includes('/api/admin/check')) {
      return fulfill(route, { valid: staff });
    }
    if (url.includes('/api/marketing/inventory-context')) {
      return fulfill(route, { success: true, homes });
    }
    if (url.includes('/api/inventory?')) {
      return fulfill(route, { success: true, inventory: staff ? [STAFF_HOME] : [] });
    }
    if (url.includes('/tour') && method === 'GET') {
      return fulfill(route, {
        success: true,
        matterport_id: savedId || null,
        matterport_url: savedId
          ? `https://my.matterport.com/show/?m=${savedId}&play=1`
          : null,
      });
    }
    if (url.includes('/tour') && method === 'POST') {
      lastPost = request.postData() || '';
      if (lastPost.includes('<') || lastPost.includes('javascript:')) {
        return fulfill(route, { detail: 'rejected' }, 400);
      }
      savedId = 'JbALitnto3g';
      return fulfill(route, {
        success: true,
        matterport_id: savedId,
        matterport_url: 'https://my.matterport.com/show/?m=JbALitnto3g&play=1',
      });
    }
    if (url.includes('/tour') && method === 'DELETE') {
      savedId = '';
      return fulfill(route, { success: true, matterport_id: null, matterport_url: null });
    }
    if (url.includes('/photos')) return fulfill(route, { photos: [] });
    return fulfill(route, { success: true });
  });
  return {
    lastPost: () => lastPost,
  };
}

test('staff can save and remove a 3D tour link on a phone-sized screen', async ({ page }) => {
  const api = await installApi(page, { staff: true, homes: [STAFF_HOME] });
  await page.goto('/photos', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Add Photos to Homes' })).toBeVisible();

  await page.getByRole('radio', { name: /Cedar Ridge/i }).click();
  const tour = page.getByRole('region', { name: '3D tour link' });
  await expect(tour).toBeVisible();
  await expect(tour).toContainText('No 3D tour saved for this home yet.');

  const input = tour.getByLabel('Matterport link');
  const box = await input.boundingBox();
  const viewport = page.viewportSize();
  expect(box?.width ?? 0).toBeGreaterThan(200);
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);

  const junk = '<script>alert(1)</script>';
  await input.fill(junk);
  await tour.getByRole('button', { name: 'Save 3D tour' }).click();
  const error = tour.getByRole('alert');
  await expect(error).toContainText('That is not a Matterport tour link.');
  await expect(error).not.toContainText('<script');
  expect(api.lastPost()).toContain(junk);
  expect(api.lastPost()).not.toContain('JbALitnto3g');

  const pasted = 'https://my.matterport.com/show/?m=JbALitnto3g&play=1&next=https://evil.example';
  await input.fill(pasted);
  await tour.getByRole('button', { name: 'Save 3D tour' }).click();
  await expect(tour.getByRole('status')).toContainText(
    '3D tour saved. Customers will see it on the website shortly.',
  );
  await expect(tour.getByRole('link', { name: 'Open 3D tour' })).toHaveAttribute(
    'href',
    'https://my.matterport.com/show/?m=JbALitnto3g&play=1',
  );
  expect(api.lastPost()).toContain(pasted);

  await tour.getByRole('button', { name: 'Remove 3D tour' }).click();
  await page.getByRole('button', { name: 'Yes, remove the tour' }).click();
  await expect(tour.getByRole('status')).toContainText('3D tour link removed.');
  await expect(tour).toContainText('No 3D tour saved for this home yet.');
});

test('the public inventory feed shows a 3D tour only when a home has a Matterport id', async ({ page }) => {
  await installApi(page, { staff: false, homes: PUBLIC_HOMES });
  await page.goto('/inventory', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Home Inventory in Huffman, TX' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open 3D tour for Oak Plan' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open 3D tour for Cedar Ridge' })).toHaveCount(0);
});
