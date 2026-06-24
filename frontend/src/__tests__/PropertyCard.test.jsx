import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PropertyCard from '../components/PropertyCard';

const sampleProperty = {
  model_name: 'Sapphire 3-Bedroom',
  manufacturer: 'TMHA',
  classification: 'Single-Wide',
  image_url: '',
  gallery_images: [],
  specs: { beds: 3, baths: 2, sq_ft: 1200 },
  pricing: { display_price: '$89,900' },
};

describe('PropertyCard', () => {
  it('renders the model name and display price', () => {
    render(
      <PropertyCard
        property={sampleProperty}
        onToggleCompare={() => {}}
        isSelected={false}
      />,
    );

    expect(screen.getByText(sampleProperty.model_name)).toBeInTheDocument();
    expect(screen.getByText('$89,900')).toBeInTheDocument();
  });

  it('fires onToggleCompare when the Compare action is clicked', () => {
    const onToggleCompare = vi.fn();
    render(
      <PropertyCard
        property={sampleProperty}
        onToggleCompare={onToggleCompare}
        isSelected={false}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /compare/i }));

    expect(onToggleCompare).toHaveBeenCalledTimes(1);
    expect(onToggleCompare).toHaveBeenCalledWith(sampleProperty);
  });

  describe('order-only / build-to-order homes (no real photos)', () => {
    const orderOnlyProperty = {
      ...sampleProperty,
      model_name: 'The Nassau',
      image_url: '',
      gallery_images: [],
      real_photos: [],
      is_orderable: true,
      is_in_stock: false,
    };

    it('shows a Build-to-Order badge and a branded placeholder (no broken img) for orderable homes with no photos', () => {
      const { container } = render(
        <PropertyCard
          property={orderOnlyProperty}
          onToggleCompare={() => {}}
          isSelected={false}
        />,
      );

      // Branded badge readable by sighted users + screen readers. Both the
      // corner badge ("Order-Only Model") and the placeholder caption
      // ("Build-to-Order") communicate the same intent, so >=1 match is expected.
      expect(screen.getAllByText(/build-to-order|order-only model/i).length).toBeGreaterThan(0);

      // No <img> element is rendered for a photo-less home — we render a
      // branded placeholder instead of a broken image.
      expect(container.querySelector('img')).toBeNull();

      // Accessible placeholder: an image-role node with descriptive alt text.
      const placeholder = screen.getByRole('img', { name: /build-to-order|order-only|built to order/i });
      expect(placeholder).toBeInTheDocument();
    });

    it('does NOT show the order-only badge for homes that DO have photos', () => {
      const withPhotos = {
        ...orderOnlyProperty,
        image_url: 'https://cdn.example.com/homes/nassau/exterior-front.jpg',
        real_photos: ['https://cdn.example.com/homes/nassau/exterior-front.jpg'],
      };

      const { container } = render(
        <PropertyCard
          property={withPhotos}
          onToggleCompare={() => {}}
          isSelected={false}
        />,
      );

      // The real exterior photo is rendered.
      const img = container.querySelector('img');
      expect(img).not.toBeNull();
      expect(img.getAttribute('src')).toContain('exterior-front.jpg');

      // No build-to-order badge when a real photo exists.
      expect(screen.queryByText(/build-to-order|order-only model/i)).toBeNull();
    });

    it('does NOT show the order-only badge for an in-stock home that happens to lack photos', () => {
      const inStockNoPhoto = {
        ...sampleProperty,
        image_url: '',
        gallery_images: [],
        real_photos: [],
        is_orderable: false,
        is_in_stock: true,
      };

      render(
        <PropertyCard
          property={inStockNoPhoto}
          onToggleCompare={() => {}}
          isSelected={false}
        />,
      );

      expect(screen.queryByText(/build-to-order|order-only model/i)).toBeNull();
    });
  });
});
