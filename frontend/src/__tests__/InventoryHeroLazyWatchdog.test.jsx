import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { HomeCard } from '../pages/InventoryBrowse';

// Regression: reported by THO staff (Celeste, 2026-08-14 / 08-18 / 08-25) —
// "This seems like we do not have photos but then when I click on this, the
// photos appear."
//
// Cause: a 4.5s watchdog started at MOUNT and marked the hero 'failed', while
// the <img> is loading="lazy" and had not even been requested yet for
// below-the-fold cards. The failed state unmounted the <img>, so the photo
// could never arrive — the card claimed "Photos Coming Soon" while its own
// badge advertised 21 photos. The detail modal has no watchdog, so clicking
// showed the photos.
const PHOTO = 'https://cdn.example.com/homes/nassau/exterior-front.jpg';

const baseHome = {
  id: 'tho-lazy-001',
  model_name: 'The Nassau',
  manufacturer: 'New Vision Manufacturing',
  status: 'Available',
  inventory_kind: 'available_now',
  is_orderable: false,
  is_in_stock: true,
  display_price: 'Call for Price',
  specs: { beds: 3, baths: 2, sq_ft: 1456 },
  image_url: PHOTO,
  real_photos: [PHOTO],
  gallery_images: [],
};

const noop = () => {};

function renderCard(home = baseHome) {
  return render(
    <HomeCard home={home} onClick={noop} onGetPrice={noop} isFavorite={false} onToggleFavorite={noop} />,
  );
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('HomeCard hero — lazy image must not be failed by the watchdog', () => {
  it('keeps the <img> mounted after the watchdog fires so a slow lazy photo can still arrive', () => {
    const { container } = renderCard();
    expect(container.querySelector('img')).not.toBeNull();

    // Watchdog window elapses before the lazy image is ever fetched.
    act(() => { vi.advanceTimersByTime(6000); });

    // The image must still be in the DOM — otherwise it can never load.
    expect(container.querySelector('img')).not.toBeNull();
  });

  it('recovers to the photo when a slow image loads after the watchdog fired', () => {
    const { container } = renderCard();
    act(() => { vi.advanceTimersByTime(6000); });

    const img = container.querySelector('img');
    expect(img).not.toBeNull();

    // Photo finally arrives (card scrolled into view).
    act(() => { fireEvent.load(img); });

    // No stale "unavailable" messaging once the photo is showing.
    expect(screen.queryByText(/photo unavailable/i)).toBeNull();
    expect(screen.queryByText(/photos coming soon/i)).toBeNull();
    expect(container.querySelector('img')).not.toBeNull();
  });

  it('still unmounts the <img> on a genuine load error', () => {
    const { container } = renderCard();
    act(() => { fireEvent.error(container.querySelector('img')); });
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/photo unavailable/i)).toBeInTheDocument();
  });
});
