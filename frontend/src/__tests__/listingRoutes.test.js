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
