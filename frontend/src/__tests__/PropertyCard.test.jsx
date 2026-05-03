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
});
