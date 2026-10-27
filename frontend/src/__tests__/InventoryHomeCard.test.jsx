import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { HomeCard } from '../pages/InventoryBrowse';

// Minimal home fixtures mirroring /api/marketing/inventory-context shape.
const baseHome = {
  id: 'tho-test-001',
  model_name: 'The Nassau',
  manufacturer: 'New Vision Manufacturing',
  status: 'Orderable',
  display_price: 'Call for Price',
  specs: { beds: 3, baths: 2, sq_ft: 1456 },
};

const noop = () => {};

function renderCard(home, overrides = {}) {
  return render(
    <HomeCard
      home={home}
      onClick={noop}
      onGetPrice={noop}
      isFavorite={false}
      onToggleFavorite={noop}
      {...overrides}
    />,
  );
}

describe('InventoryBrowse HomeCard — order-only / build-to-order homes', () => {
  it('shows a branded Build-to-Order placeholder + badge (no broken img) when orderable and photo-less', () => {
    const { container } = renderCard({
      ...baseHome,
      is_orderable: true,
      is_in_stock: false,
      image_url: '',
      real_photos: [],
      gallery_images: [],
    });

    // Badge + caption communicate intent; at least one match expected.
    expect(screen.getAllByText(/build-to-order|order-only model/i).length).toBeGreaterThan(0);

    // Branded placeholder rendered (no broken <img>).
    expect(container.querySelector('img')).toBeNull();
    const placeholder = screen.getByRole('img', { name: /build-to-order|built to order|order-only/i });
    expect(placeholder).toBeInTheDocument();
  });

  it('renders the real photo and no order-only badge when the home HAS photos', () => {
    const { container } = renderCard({
      ...baseHome,
      is_orderable: true,
      image_url: 'https://cdn.example.com/homes/nassau/exterior-front.jpg',
      real_photos: ['https://cdn.example.com/homes/nassau/exterior-front.jpg'],
      gallery_images: [],
    });

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img.getAttribute('src')).toContain('exterior-front.jpg');
    expect(screen.queryByText(/build-to-order|order-only model/i)).toBeNull();
  });

  it('falls back to the branded Build-to-Order placeholder when an orderable hero fails', () => {
    const { container } = renderCard({
      ...baseHome,
      is_orderable: true,
      image_url: 'https://cdn.example.com/broken-orderable-hero.jpg',
      real_photos: ['https://cdn.example.com/broken-orderable-hero.jpg'],
      gallery_images: [],
    });

    fireEvent.error(container.querySelector('img'));

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('img', { name: /build-to-order|built to order/i })).toBeInTheDocument();
  });

  it('does NOT brand an in-stock home that merely lacks photos as order-only', () => {
    renderCard({
      ...baseHome,
      status: 'Available',
      inventory_kind: 'available_now',
      is_orderable: false,
      is_in_stock: true,
      image_url: '',
      real_photos: [],
      gallery_images: [],
    });

    expect(screen.queryByText(/build-to-order|order-only model/i)).toBeNull();
    // Falls back to the generic "Photos Coming Soon" empty state.
    expect(screen.getByText(/photos coming soon/i)).toBeInTheDocument();
  });

  it('shows call, text, and apply, and only that used home’s photo', () => {
    const { container } = renderCard({
      ...baseHome,
      id: '43945',
      status: 'Pre-Owned',
      inventory_kind: 'pre_owned',
      is_orderable: false,
      image_url: 'https://images.unsplash.com/photo-1',
      real_photos: [
        'https://storage.googleapis.com/tho-inventory-assets/inventory/43945/hero.jpg',
        'https://images.unsplash.com/photo-1',
        'https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/other.jpg',
      ],
      gallery_images: ['https://images.unsplash.com/photo-1'],
    });

    expect(screen.getByRole('link', { name: /Call \(281\) 324-3020/i })).toHaveAttribute(
      'href',
      'tel:+12813243020',
    );
    expect(screen.getByRole('link', { name: /Text \(281\) 324-3020/i }).getAttribute('href')).toMatch(
      /^sms:\+12813243020/,
    );
    expect(screen.getByRole('button', { name: /Ask about financing/i })).toBeInTheDocument();
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img.getAttribute('src')).toContain('/inventory/43945/hero.jpg');
    expect(container.innerHTML).not.toContain('unsplash');
    expect(container.innerHTML).not.toContain('99999');
  });

  it('shows that used home’s floor plan when it has only disallowed photos', () => {
    const { container } = renderCard({
      ...baseHome,
      id: '28527',
      status: 'Pre-Owned',
      inventory_kind: 'pre_owned',
      is_orderable: false,
      image_url: 'https://images.unsplash.com/photo-1',
      real_photos: [
        'https://images.unsplash.com/photo-1',
        'https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/99999/other.jpg',
        'https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg',
      ],
      gallery_images: ['https://images.unsplash.com/photo-1'],
      floorplan_url: 'https://lot.example/plan.jpg',
      media_quality: { status: 'floorplan_only' },
    });

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img.getAttribute('src')).toBe('https://lot.example/plan.jpg');
    expect(img.className).toContain('object-contain');
    expect(screen.getByText('Floor plan')).toBeInTheDocument();
    expect(screen.queryByText(/photos coming soon/i)).toBeNull();
    expect(container.innerHTML).not.toContain('unsplash');
    expect(container.innerHTML).not.toContain('99999');
    expect(container.innerHTML).not.toContain('showroom.jpg');
  });

  it('shows the floor-plan drawing when an orderable home has no photo', () => {
    const plan = 'https://cdn.example/2306P.png';
    const { container } = renderCard({
      ...baseHome,
      model_name: 'Compass / Ocampo 2306P',
      is_orderable: true,
      image_url: '',
      real_photos: [],
      gallery_images: [],
      floorplan_url: plan,
    });

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img.getAttribute('src')).toBe(plan);
    expect(img.className).toContain('object-contain');
    expect(screen.getByText('Floor plan')).toBeInTheDocument();
    expect(screen.queryByText(/build-to-order/i)).toBeNull();
  });

  it('uses the floor plan after a manufacturer photo fails to load', () => {
    const dead = 'https://cdn.example/absolute-value-1.jpg';
    const plan = 'https://cdn.example/floor-plans-small.jpg';
    const { container } = renderCard({
      ...baseHome,
      model_name: 'Solution / The Absolute Value SLC28764A',
      is_orderable: true,
      image_url: dead,
      real_photos: [dead],
      gallery_images: [],
      floorplan_url: plan,
    });

    fireEvent.error(container.querySelector('img'));

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img.getAttribute('src')).toBe(plan);
    expect(screen.getByText('Floor plan')).toBeInTheDocument();
  });

  it('removes a floor plan that fails to load instead of leaving a broken image', () => {
    const plan = 'https://cdn.example/broken-plan.jpg';
    const { container } = renderCard({
      ...baseHome,
      is_orderable: true,
      image_url: '',
      real_photos: [],
      gallery_images: [],
      floorplan_url: plan,
    });

    fireEvent.error(container.querySelector('img'));

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('img', { name: /build-to-order|built to order/i })).toBeInTheDocument();
  });

  it('puts a Watch 3D tour button on the card when the home has a tour', () => {
    const onWatchTour = vi.fn();
    renderCard({
      ...baseHome,
      matterport_id: 'SvVRKXdXUQq',
      image_url: 'https://cdn.example.com/homes/nassau/exterior-front.jpg',
      real_photos: ['https://cdn.example.com/homes/nassau/exterior-front.jpg'],
    }, { onWatchTour });

    fireEvent.click(screen.getByRole('button', { name: /watch 3d tour/i }));
    expect(onWatchTour).toHaveBeenCalledTimes(1);
    const tour = screen.getByRole('link', { name: /open 3d tour for the nassau/i });
    expect(tour).toHaveAttribute('href', 'https://my.matterport.com/show/?m=SvVRKXdXUQq&play=1');
    expect(tour).toHaveAttribute('target', '_blank');
  });

  it('hides the tour button when the home has no Matterport id', () => {
    renderCard({
      ...baseHome,
      image_url: 'https://cdn.example.com/homes/nassau/exterior-front.jpg',
      real_photos: ['https://cdn.example.com/homes/nassau/exterior-front.jpg'],
    });
    expect(screen.queryByRole('button', { name: /watch 3d tour/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /watch 3d tour/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /open 3d tour/i })).toBeNull();
  });

  it('opens the first-party quote form instead of navigating to a legacy quote URL', () => {
    const onGetPrice = vi.fn();
    const { container } = renderCard({
      ...baseHome,
      quote_url: '/quote/floorplan/123/dealer/3522',
    }, { onGetPrice });

    const quoteButton = screen.getByRole('button', { name: /Check Price & Availability/i });
    expect(container.querySelector('a[href^="/quote/"]')).toBeNull();
    fireEvent.click(quoteButton);

    expect(onGetPrice).toHaveBeenCalledTimes(1);
  });

  it('labels listed stock as requiring an availability check', () => {
    renderCard({
      ...baseHome,
      status: 'Available',
      inventory_kind: 'available_now',
      is_orderable: false,
      image_url: '',
      real_photos: [],
      gallery_images: [],
    });

    expect(screen.getByText('Check availability')).toBeInTheDocument();
  });
});
