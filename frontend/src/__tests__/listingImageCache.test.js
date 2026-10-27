import { describe, expect, it } from 'vitest';
import { shouldCacheListingImage } from '../../listingImageCache';

const CDN = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/2045/floorplan/223034/Skyliner-4732B-Kitchen-1.jpg';
const GCS = 'https://storage.googleapis.com/tho-inventory-assets/inventory/28102/hero.jpg';
const STAFF = 'https://www.texashomeoutlet.com/api/inventory/photos/28102/lot.jpg';

describe('service worker listing image cache', () => {
  it('leaves manufacturer and dealer CDN images to the browser', () => {
    expect(shouldCacheListingImage(new URL(CDN), { destination: 'image' })).toBe(false);
    expect(shouldCacheListingImage(new URL(GCS), { destination: 'image' })).toBe(false);
  });

  it('caches same-origin staff listing photos', () => {
    expect(shouldCacheListingImage(new URL(STAFF), { destination: 'image' })).toBe(true);
    expect(shouldCacheListingImage(STAFF, { destination: 'image' })).toBe(true);
  });

  it('does not cache non-image requests for the photo path', () => {
    expect(shouldCacheListingImage(new URL(STAFF), { destination: 'document' })).toBe(false);
    expect(shouldCacheListingImage(new URL(STAFF), { destination: '' })).toBe(false);
    expect(shouldCacheListingImage(null, { destination: 'image' })).toBe(false);
  });
});
