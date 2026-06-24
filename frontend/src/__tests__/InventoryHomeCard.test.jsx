import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
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

function renderCard(home) {
  return render(
    <HomeCard
      home={home}
      onClick={noop}
      onScheduleTour={noop}
      isFavorite={false}
      onToggleFavorite={noop}
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
});
