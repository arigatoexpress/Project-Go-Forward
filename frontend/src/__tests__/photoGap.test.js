import { describe, expect, it, vi } from 'vitest';
import {
  PHOTO_GAP_LABELS,
  blockedPhotoMessage,
  checkCustomerPhotos,
  isUploadablePhotoFile,
  checkPhotoUrl,
  customerPhotoUrls,
  mergeStaffAndPublicHomes,
  photoGapReason,
  photoHomeListMessage,
  photoUrlsToCheck,
  staffPhotoHomeDetails,
  staffPhotoHomeName,
  visiblePhotoHomes,
} from '../utils/photoGap';

const floorplan = 'https://cdn.example/manufacturer/2045/floorplan/223034/4732-floor-plans.jpg';
const kitchen = 'https://cdn.example/manufacturer/2045/floorplan/223034/Skyliner-4732B-Kitchen-1.jpg';
const dead = 'https://cdn.example/manufacturer/2045/floorplan/223034/dead.jpg';

describe('photo gap', () => {
  it('treats a floor plan as still needing a photo', () => {
    const home = {
      id: 'floorplan-223034',
      model_name: 'Skyliner / 4732B',
      inventory_kind: 'orderable_floorplan',
      is_orderable: true,
      status: 'Orderable',
      real_photos: [],
      image_url: floorplan,
      floorplan_url: floorplan,
      floorplan_urls: [floorplan],
    };
    expect(customerPhotoUrls(home)).toEqual([]);
    expect(photoGapReason(home)).toBe('floorplan');
    expect(PHOTO_GAP_LABELS.floorplan).toBe('Only a floor plan');
    expect(staffPhotoHomeName(home)).toBe('Skyliner 4732B');
    expect(staffPhotoHomeDetails(home)).toBe('Orderable plan · Only a floor plan');
  });

  it('keeps a manufacturer picture that customers can see', () => {
    const home = {
      id: 'floorplan-223034',
      model_name: 'Skyliner / 4732B',
      is_orderable: true,
      real_photos: [kitchen],
      floorplan_url: floorplan,
      floorplan_urls: [floorplan],
    };
    expect(customerPhotoUrls(home)).toEqual([kitchen]);
    expect(photoGapReason(home)).toBeNull();
    expect(photoUrlsToCheck(home, new Set(), new Set(), new Set())).toEqual([kitchen]);
  });

  it('flags a picture that failed to load, and stops once any picture loads', () => {
    const home = {
      id: 'hidden',
      model_name: 'The Aspen',
      status: 'AVAILABLE',
      real_photos: [dead, kitchen],
    };
    expect(photoGapReason(home, new Set())).toBeNull();
    expect(photoGapReason(home, new Set([dead]))).toBeNull();
    expect(photoGapReason(home, new Set([dead, kitchen]))).toBe('hidden');
    expect(photoUrlsToCheck(home, new Set([dead]), new Set([kitchen]), new Set())).toEqual([]);
  });

  it('does not ask for a new photo after staff have saved one', () => {
    const home = {
      id: '43372',
      model_name: 'Starter Home',
      status: 'AVAILABLE',
      has_staff_photos: true,
      real_photos: ['/api/inventory/photos/43372/lot.jpg', dead],
    };
    expect(photoGapReason(home, new Set([dead]))).toBeNull();
    expect(photoUrlsToCheck(home, new Set(), new Set(), new Set())).toEqual([]);
  });

  it('ignores the site icon placeholder', () => {
    const home = {
      id: 'plain',
      model_name: 'No Pictures',
      status: 'AVAILABLE',
      image_url: '/tex-icon.svg',
      image_placeholder: true,
      real_photos: [],
    };
    expect(customerPhotoUrls(home)).toEqual([]);
    expect(photoGapReason(home)).toBe('missing');
    expect(staffPhotoHomeName({ model_name: 'PRE-OWNED / Big Blue' })).toBe('Big Blue');
    expect(staffPhotoHomeName({ model_name: 'The Promotional Series / The Nassau FAC28483A' })).toBe('The Nassau');
  });

  it('merges the public catalog with staff-only homes and keeps a saved photo', () => {
    const merged = mergeStaffAndPublicHomes(
      [{ id: 'floorplan-1', model_name: 'Skyliner / 4732B', real_photos: [] }],
      [
        { id: 'floorplan-1', model_name: 'Skyliner / 4732B', has_staff_photos: true, real_photos: ['/api/inventory/photos/floorplan-1/lot.jpg'] },
        { id: 'dup', model_name: 'Copy', duplicate_of: 'floorplan-1', real_photos: [] },
        { id: 'staff-only', model_name: 'Office Special', status: 'AVAILABLE', real_photos: [] },
      ],
    );
    expect(merged.map((home) => home.id)).toEqual(['floorplan-1', 'staff-only']);
    expect(merged[0].has_staff_photos).toBe(true);
    expect(photoGapReason(merged[0])).toBeNull();
    expect(photoGapReason(merged[1])).toBe('missing');
  });

  it('defaults the visible list to homes that still need photos', () => {
    const homes = [
      { id: 'ready', model_name: 'The Nassau', status: 'AVAILABLE', real_photos: [kitchen] },
      { id: 'sold', model_name: 'Old Sold Home', status: 'SOLD', real_photos: [] },
      { id: 'gap', model_name: 'Big Blue', status: 'AVAILABLE', stock_number: '44490', real_photos: [] },
    ];
    expect(visiblePhotoHomes(homes, { onlyNeedsPhotos: true }).map((home) => home.id)).toEqual(['gap']);
    expect(visiblePhotoHomes(homes, { onlyNeedsPhotos: false, includeInactive: true }).map((home) => home.id))
      .toEqual(['gap', 'sold', 'ready']);
    expect(photoHomeListMessage({
      listedCount: 2,
      visibleCount: 0,
      onlyNeedsPhotos: true,
      checkingPhotos: false,
    })).toBe('Every home already has a photo customers can see.');
    expect(photoHomeListMessage({ listedCount: 0, visibleCount: 0 })).toMatch(/Include sold\/archived homes/);
  });

  it('checks pictures until one loads and records the ones that do not', async () => {
    const home = { id: 'hidden', model_name: 'The Aspen', status: 'AVAILABLE', real_photos: [dead, kitchen] };
    const failed = new Set();
    const seen = [];
    await checkCustomerPhotos([home], {
      failedUrls: failed,
      okUrls: new Set(),
      onFailed: (url) => seen.push(url),
      check: async (url) => url !== dead,
    });
    expect(seen).toEqual([dead]);
    expect(photoGapReason(home, failed)).toBeNull();

    const bothDead = { ...home, real_photos: [dead, `${dead}?2`] };
    const allFailed = new Set();
    await checkCustomerPhotos([bothDead], {
      failedUrls: allFailed,
      check: async () => false,
    });
    expect(photoGapReason(bothDead, allFailed)).toBe('hidden');
  });

  it('resolves a picture check from load, error, or cancel', async () => {
    class FakeImage {
      set src(value) {
        if (String(value).includes('dead')) this.onerror();
        else this.onload();
      }
    }
    await expect(checkPhotoUrl(kitchen, { ImageCtor: FakeImage, timeoutMs: 50 })).resolves.toBe(true);
    await expect(checkPhotoUrl(dead, { ImageCtor: FakeImage, timeoutMs: 50 })).resolves.toBe(false);
    const controller = new AbortController();
    controller.abort();
    await expect(checkPhotoUrl(kitchen, { ImageCtor: FakeImage, signal: controller.signal })).resolves.toBe(null);
  });

  it('accepts a phone photo whose type was left blank', () => {
    expect(isUploadablePhotoFile({ name: 'IMG_1234.jpg', type: '' })).toBe(true);
    expect(isUploadablePhotoFile({ name: 'front.png', type: 'application/octet-stream' })).toBe(true);
    expect(isUploadablePhotoFile({ name: 'notes.txt', type: 'text/plain' })).toBe(false);
    expect(isUploadablePhotoFile({ name: 'IMG_1234.HEIC', type: 'image/heic' })).toBe(false);
    expect(blockedPhotoMessage([{ name: 'IMG_1234.HEIC', type: 'image/heic' }])).toMatch(/HEIC format/);
    expect(blockedPhotoMessage([{ name: 'notes.txt', type: 'text/plain' }])).toMatch(/Use JPG, PNG, WebP, or GIF/);
  });

  it('does not call the checker after cancel', async () => {
    const check = vi.fn(async () => false);
    await checkCustomerPhotos(
      [{ id: '1', model_name: 'Home', status: 'AVAILABLE', real_photos: [dead] }],
      { check, isCancelled: () => true },
    );
    expect(check).not.toHaveBeenCalled();
  });
});
