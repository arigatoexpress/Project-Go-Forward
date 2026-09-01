import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { HomeCard, listingPhotoRank } from '../pages/InventoryBrowse';

// Regression: staff reported after the 2026-08-31 inventory cutover that
// "some listings still have floor plans first or no pictures of houses".
//
// Two causes, both verified against the live catalog:
//  1. Seeded heroes at tho-inventory-assets/inventory/<id>/hero.jpg sit outside
//     the manufacturer floorplan namespace and are named "hero", so
//     isFloorplanImage cannot recognise them. Four listed homes had a floorplan
//     DRAWING as their card image (confirmed by eye on 28527, a pre-owned home).
//  2. 22 hero URLs answer HTTP 403 — a listing with five good photos rendered
//     "Photo Unavailable" because only the FIRST one was ever tried.
const EXT = 'https://cdn.example.com/dealer/1/inventory/9/Nassau-ext-1.jpg';
const KITCHEN = 'https://cdn.example.com/dealer/1/inventory/9/Nassau-kitchen-2.jpg';
const SEEDED_HERO = 'https://storage.googleapis.com/tho-inventory-assets/inventory/9/hero.jpg';

const baseHome = {
  id: 'tho-rank-001',
  model_name: 'The Nassau',
  manufacturer: 'New Vision Manufacturing',
  status: 'Available',
  inventory_kind: 'available_now',
  is_orderable: false,
  is_in_stock: true,
  display_price: 'Call for Price',
  specs: { beds: 3, baths: 2, sq_ft: 1456 },
};

const noop = () => {};
const renderCard = (home) => render(
  <HomeCard home={home} onClick={noop} onGetPrice={noop} isFavorite={false} onToggleFavorite={noop} />,
);

describe('listingPhotoRank', () => {
  it('ranks an exterior shot ahead of an interior, and both ahead of an unlabeled file', () => {
    expect(listingPhotoRank(EXT)).toBe(0);
    expect(listingPhotoRank(KITCHEN)).toBe(1);
    expect(listingPhotoRank(SEEDED_HERO)).toBe(2);
    expect(listingPhotoRank('https://cdn.example.com/manufacturer/1/floorplan/2/1.jpg')).toBe(2);
  });
});

describe('HomeCard hero selection', () => {
  it('leads with a photo of the house even when an unlabeled "hero" file is listed first', () => {
    const { container } = renderCard({
      ...baseHome,
      image_url: SEEDED_HERO,
      real_photos: [SEEDED_HERO, KITCHEN, EXT],
      gallery_images: [],
    });
    // The seeded hero.jpg is a floorplan drawing on real listings — it must not
    // win the card just because it happens to be first.
    expect(container.querySelector('img').getAttribute('src')).toBe(EXT);
  });

  it('prefers an interior photo over an unlabeled file when no exterior exists', () => {
    const { container } = renderCard({
      ...baseHome,
      image_url: SEEDED_HERO,
      real_photos: [SEEDED_HERO, KITCHEN],
      gallery_images: [],
    });
    expect(container.querySelector('img').getAttribute('src')).toBe(KITCHEN);
  });

  it('falls through to the next photo when the first one is dead (403)', () => {
    const { container } = renderCard({
      ...baseHome,
      image_url: EXT,
      real_photos: [EXT, KITCHEN],
      gallery_images: [],
    });
    const img = container.querySelector('img');
    expect(img.getAttribute('src')).toBe(EXT);

    fireEvent.error(img); // vendor CDN returns 403 for this one

    const next = container.querySelector('img');
    expect(next).not.toBeNull();
    expect(next.getAttribute('src')).toBe(KITCHEN);
    expect(screen.queryByText(/photo unavailable/i)).toBeNull();
  });

  it('only gives up once every candidate has failed', () => {
    const { container } = renderCard({
      ...baseHome,
      image_url: EXT,
      real_photos: [EXT, KITCHEN],
      gallery_images: [],
    });
    fireEvent.error(container.querySelector('img'));
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/photo unavailable/i)).toBeInTheDocument();
  });
});
