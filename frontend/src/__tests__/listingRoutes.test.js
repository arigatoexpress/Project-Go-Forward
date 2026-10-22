import { describe, expect, it } from 'vitest';
import {
  fallbackListingHeading,
  getCityLanding,
  isInstockHome,
  listingPagePhotos,
  listingImageAlt,
  rewriteMediaUrl,
  listingPath,
  listingPhotoSubject,
  listingShareUrl,
  listingSlug,
  publicCanonicalOrigin,
  PUBLIC_CANONICAL_ORIGIN,
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

  it('keeps the staff upload URL the homes editor produces', () => {
    const upload = '/api/inventory/photos/43945/lot-front-0123456789abcdef01234567.jpg';
    const used = {
      id: '43945',
      inventory_kind: 'pre_owned',
      status: 'Pre-Owned',
      image_url: upload,
      real_photos: [
        upload,
        'https://images.unsplash.com/photo-1',
        'https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/other.jpg',
        'https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg',
      ],
      gallery_images: ['https://images.unsplash.com/photo-1'],
      floor_plan_url: 'https://lot.example/plan.jpg',
    };
    expect(listingPagePhotos(used)).toEqual([upload]);
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

  it('rewrites the dead Rosewood numbered jpg to the working .JPG sibling', () => {
    const dead = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3328/floorplan/232969/1.jpg';
    const live = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3328/floorplan/232969/1.JPG';
    expect(rewriteMediaUrl(dead)).toBe(live);
    expect(rewriteMediaUrl(`${dead}?v=1`)).toBe(`${live}?v=1`);
    expect(rewriteMediaUrl(live)).toBe(live);
    const home = {
      id: '41976',
      status: 'Available',
      inventory_kind: 'available_now',
      image_url: '/api/inventory/photos/41976/lot.jpg',
      real_photos: ['/api/inventory/photos/41976/lot.jpg', live, dead],
    };
    expect(listingPagePhotos(home)).toEqual(['/api/inventory/photos/41976/lot.jpg', live]);
  });

  it('repairs a confirmed extension sibling and leaves a live jpg alone', () => {
    const dead = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/227079/5.jpg';
    const live = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/227079/5.png';
    const alreadyLive = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/232844/anderson-1.jpg';
    const noSibling = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/232175/1.jpg';
    expect(rewriteMediaUrl(dead)).toBe(live);
    expect(rewriteMediaUrl(alreadyLive)).toBe(alreadyLive);
    expect(rewriteMediaUrl(noSibling)).toBe(noSibling);
    expect(rewriteMediaUrl(
      'https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/floorplan/236155/Toledo%20Living%20Room%20%287%29rfw.jpg',
    )).toBe(
      'https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/floorplan/236155/Toledo%20Living%20Room%20%287%29rfw.webp',
    );
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

  it('names a listing photo from filename parts and keeps alts unique', () => {
    const home = { model_name: 'Premier / Creole 3256H32447' };
    const exterior = 'https://cdn.example/dealer/3522/inventory/43372/Creole%203256H32447-ext-1.jpg';
    const kitchen = 'https://cdn.example/dealer/3522/inventory/43372/Creole%203256H32447-kit-1.jpg';
    expect(listingPhotoSubject(exterior)).toBe('exterior');
    expect(listingPhotoSubject(kitchen)).toBe('kitchen');
    expect(listingPhotoSubject('https://storage.example/inventory/43372/hero.jpg')).toBe('');
    expect(listingImageAlt(home, exterior, 1, 2)).toBe(
      'Premier / Creole 3256H32447 — exterior photo 1 at Texas Home Outlet in Huffman, TX',
    );
    expect(listingImageAlt(home, kitchen, 2, 2)).toContain('kitchen photo 2');
    expect(listingImageAlt(home, 'https://lot.example/plan.jpg', 1, 1, { floorplan: true })).toBe(
      'Floor plan for Premier / Creole 3256H32447',
    );
  });

  it('shares the canonical listing URL and keeps apex hosts on www', () => {
    const home = {
      id: '44490',
      model_name: 'PRE-OWNED / Big Blue',
      status: 'Pre-Owned',
      inventory_kind: 'pre_owned',
    };
    expect(listingShareUrl(home)).toMatch(/\/homes\/44490-pre-owned-big-blue$/);
    expect(listingShareUrl(home)).not.toContain('?home=');
    expect(publicCanonicalOrigin('texashomeoutlet.com', 'https://texashomeoutlet.com')).toBe(
      PUBLIC_CANONICAL_ORIGIN,
    );
    expect(publicCanonicalOrigin('tho.sapphirealpha.xyz', 'https://tho.sapphirealpha.xyz')).toBe(
      PUBLIC_CANONICAL_ORIGIN,
    );
    expect(publicCanonicalOrigin('www.texashomeoutlet.com', 'https://www.texashomeoutlet.com')).toBe(
      'https://www.texashomeoutlet.com',
    );
    expect(publicCanonicalOrigin('localhost', 'http://localhost:3000')).toBe('http://localhost:3000');
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
