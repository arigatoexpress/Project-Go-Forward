import { describe, expect, it } from 'vitest';
import {
  fallbackListingHeading,
  getCityLanding,
  isInstockHome,
  listingPagePhotos,
  listingPath,
  listingSlug,
  resolveHomeFromPath,
  stockId,
} from '../utils/listingRoutes';

describe('listingRoutes', () => {
  it('builds stable /homes/<stock-id>-<slug> paths for in-stock homes', () => {
    const home = {
      id: '44490',
      model_name: 'PRE-OWNED / Big Blue',
      status: 'Pre-Owned',
      inventory_kind: 'pre_owned',
    };
    expect(stockId(home)).toBe('44490');
    expect(listingSlug(home.model_name)).toBe('pre-owned-big-blue');
    expect(listingPath(home)).toBe('/homes/44490-pre-owned-big-blue');
    expect(isInstockHome({ status: 'Orderable', inventory_kind: 'orderable_floorplan' })).toBe(false);
  });

  it('keeps used-home photos to real_photos and falls back to the floor plan', () => {
    const catalog = 'https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg';
    const used = {
      inventory_kind: 'pre_owned',
      status: 'Pre-Owned',
      real_photos: ['https://lot.example/1.jpg', catalog],
      gallery_images: [catalog],
      image_url: catalog,
      floor_plan_url: 'https://lot.example/plan.jpg',
    };
    expect(listingPagePhotos(used)).toEqual(['https://lot.example/1.jpg']);
    expect(listingPagePhotos({ ...used, real_photos: [catalog] })).toEqual(['https://lot.example/plan.jpg']);
    const orderable = {
      inventory_kind: 'orderable_floorplan',
      status: 'Orderable',
      is_new: true,
      image_url: catalog,
      real_photos: [catalog],
    };
    expect(listingPagePhotos(orderable)).toEqual([catalog]);
  });

  it('keeps a used home’s own upload when the unit id case differs', () => {
    const upload = '/api/inventory/photos/Lot-Overlay/front.jpg';
    const used = {
      id: 'lot-overlay',
      inventory_kind: 'pre_owned',
      status: 'Pre-Owned',
      image_url: upload,
      real_photos: [
        upload,
        'https://images.unsplash.com/photo-1',
        'https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/other.jpg',
      ],
      floor_plan_url: 'https://lot.example/plan.jpg',
    };
    expect(listingPagePhotos(used)).toEqual([upload]);
  });

  it('falls back to the floor plan when a used home has only disallowed photos', () => {
    const used = {
      id: '28527',
      inventory_kind: 'pre_owned',
      status: 'Pre-Owned',
      image_url: 'https://images.unsplash.com/photo-1',
      real_photos: [
        'https://images.unsplash.com/photo-1',
        'https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/other.jpg',
        'https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg',
      ],
      gallery_images: ['https://images.unsplash.com/photo-1'],
      floor_plan_url: 'https://lot.example/plan.jpg',
    };
    expect(listingPagePhotos(used)).toEqual(['https://lot.example/plan.jpg']);
  });

  it('drops stock photos and another unit’s photos from a used listing', () => {
    const own = 'https://storage.googleapis.com/tho-inventory-assets/inventory/43945/hero.jpg';
    const other = 'https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/1.jpg';
    const stock = 'https://images.unsplash.com/photo-1500000000000';
    const catalog = 'https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg';
    const used = {
      id: '43945',
      inventory_kind: 'pre_owned',
      status: 'Pre-Owned',
      image_url: stock,
      real_photos: [own, other, stock, catalog],
      gallery_images: [stock],
      floor_plan_url: 'https://lot.example/plan.jpg',
    };
    expect(listingPagePhotos(used)).toEqual([own]);
  });

  it('derives a unique heading from city, home, and plan URLs before inventory loads', () => {
    expect(fallbackListingHeading('/manufactured-homes-in-humble-tx')).toBe(
      'Manufactured & Mobile Homes in Humble, TX',
    );
    expect(fallbackListingHeading('/homes/44490-pre-owned-big-blue')).toBe('Pre Owned Big Blue');
    expect(fallbackListingHeading('/plan/223034/skyliner/4732b/')).toBe('Skyliner 4732b');
  });

  it('parses city landings and resolves a home from its listing URL', () => {
    const landing = getCityLanding('/manufactured-homes-in-new-caney-tx/');
    expect(landing.city).toBe('New Caney');
    expect(landing.heading).toContain('New Caney, TX');
    const home = {
      id: '44490',
      model_name: 'PRE-OWNED / Big Blue',
      status: 'Pre-Owned',
      inventory_kind: 'pre_owned',
    };
    expect(resolveHomeFromPath([home], '/homes/44490-old-slug')).toEqual(home);
  });
});
