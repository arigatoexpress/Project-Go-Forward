import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, URL as NodeURL } from 'node:url';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(testDir, '../..');
const viteBin = path.join(frontendRoot, 'node_modules/vite/bin/vite.js');
const swPath = path.join(frontendRoot, 'dist/sw.js');

const SAME_ORIGIN = 'https://www.texashomeoutlet.com';
const STAFF = `${SAME_ORIGIN}/api/inventory/photos/28102/lot.jpg`;
const CDN = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/2045/floorplan/223034/Skyliner-4732B-Kitchen-1.jpg';
const GCS = 'https://storage.googleapis.com/tho-inventory-assets/inventory/28102/hero.jpg';
const OTHER_ORIGIN_PHOTO = 'https://cdn.example/api/inventory/photos/28102/lot.jpg';

function extractListingImageMatcher(source) {
  const needleAt = source.indexOf('/api/inventory/photos/');
  const routeAt = needleAt < 0 ? -1 : source.lastIndexOf('registerRoute(', needleAt);
  const strategyAt = needleAt < 0 ? -1 : source.indexOf('CacheFirst', needleAt);
  if (routeAt < 0 || strategyAt < 0) {
    throw new Error('dist/sw.js listing-images route has no matcher');
  }
  let end = source.lastIndexOf('new ', strategyAt);
  while (end > routeAt && /\s/.test(source[end - 1])) end -= 1;
  if (source[end - 1] === ',') end -= 1;
  return source.slice(routeAt + 'registerRoute('.length, end);
}

function runMatcher(matcherSource, href, destination) {
  const context = vm.createContext({
    self: { location: { origin: SAME_ORIGIN } },
    URL: NodeURL,
  });
  const script = new vm.Script(`
    const matcher = (${matcherSource});
    matcher({
      url: new URL(${JSON.stringify(href)}),
      request: { destination: ${JSON.stringify(destination)} },
    });
  `);
  return script.runInContext(context);
}

describe('built service worker listing image route', () => {
  it('matches only same-origin staff photos and does not reference outside helpers', () => {
    execFileSync(process.execPath, [viteBin, 'build', '--mode', 'production'], {
      cwd: frontendRoot,
      env: { ...process.env, NODE_ENV: 'production', SENTRY_AUTH_TOKEN: '' },
      stdio: 'pipe',
      timeout: 180000,
    });
    const source = readFileSync(swPath, 'utf8');
    expect(source).not.toContain('shouldCacheListingImage');
    expect(source).toMatch(/["']listing-images["']/);
    expect(source).toMatch(/maxEntries\s*:\s*120\b/);
    expect(source).toMatch(/maxAgeSeconds\s*:\s*604800\b/);
    expect(source).toMatch(/statuses\s*:\s*\[\s*0\s*,\s*200\s*\]/);
    expect(source).toContain('CacheFirst');
    expect(source).toContain('NetworkOnly');

    const matcherSource = extractListingImageMatcher(source);
    expect(matcherSource).toContain('/api/inventory/photos/');
    expect(matcherSource).toContain('self.location.origin');

    expect(runMatcher(matcherSource, STAFF, 'image')).toBe(true);
    expect(runMatcher(matcherSource, CDN, 'image')).toBe(false);
    expect(runMatcher(matcherSource, GCS, 'image')).toBe(false);
    expect(runMatcher(matcherSource, OTHER_ORIGIN_PHOTO, 'image')).toBe(false);
    expect(runMatcher(matcherSource, STAFF, 'document')).toBe(false);
  }, 180000);
});
