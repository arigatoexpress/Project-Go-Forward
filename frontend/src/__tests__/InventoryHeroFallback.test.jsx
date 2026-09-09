import { describe, it, expect, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { HomeCard } from '../pages/InventoryBrowse';

const FIRST = 'https://cdn.example.com/curated-living.jpg';
const SECOND = 'https://cdn.example.com/exterior.jpg';
const THIRD = 'https://cdn.example.com/kitchen.jpg';
const home = {
  id: 'fallback-test',
  model_name: 'Test Home',
  status: 'Available',
  inventory_kind: 'available_now',
  specs: { beds: 3, baths: 2, sq_ft: 1400 },
  image_url: FIRST,
  real_photos: [FIRST, SECOND, THIRD],
};
const card = (value = home) => <HomeCard home={value} onClick={() => {}} onGetPrice={() => {}} />;

describe('HomeCard photo fallback', () => {
  it('keeps an empty gallery as an intentional placeholder', () => {
    const { container } = render(card({ ...home, image_url: '', real_photos: [] }));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/photos coming soon/i)).toBeInTheDocument();
    expect(screen.queryByText(/photo unavailable/i)).toBeNull();
  });

  it('keeps curated order and recovers when the next eligible photo loads', () => {
    const { container } = render(card());
    expect(container.querySelector('img')).toHaveAttribute('src', FIRST);
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
    fireEvent.load(container.querySelector('img'));
    expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
    expect(screen.queryByText(/photo unavailable/i)).toBeNull();
  });

  it('tries each unique eligible photo once before showing unavailable', () => {
    const { container } = render(card({ ...home, gallery_images: [SECOND, THIRD] }));
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toHaveAttribute('src', THIRD);
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/photo unavailable/i)).toBeInTheDocument();
  });

  it('retries the refreshed gallery after the previous gallery was exhausted', () => {
    const { container, rerender } = render(card({ ...home, real_photos: [FIRST] }));
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toBeNull();
    rerender(card({ ...home, image_url: SECOND, real_photos: [SECOND, FIRST] }));
    expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toHaveAttribute('src', FIRST);
  });

  it('resets failures when the card receives another home with the same URLs', () => {
    const { container, rerender } = render(card());
    fireEvent.error(container.querySelector('img'));
    rerender(card({ ...home, id: 'another-home' }));
    expect(container.querySelector('img')).toHaveAttribute('src', FIRST);
  });

  it('never falls through to an explicitly classified floorplan', () => {
    const floorplan = 'https://cdn.example.com/floorplan.png';
    const { container } = render(card({
      ...home,
      real_photos: [FIRST, floorplan, SECOND],
      floorplan_url: floorplan,
    }));
    fireEvent.error(container.querySelector('img'));
    expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
  });

  it('keeps a slow fallback mounted until it can load', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(card());
      fireEvent.error(container.querySelector('img'));
      act(() => { vi.advanceTimersByTime(6000); });
      expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
      fireEvent.load(container.querySelector('img'));
      expect(container.querySelector('img')).toHaveAttribute('src', SECOND);
      expect(screen.queryByText(/photo unavailable/i)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
