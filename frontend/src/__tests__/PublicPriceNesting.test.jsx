import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import ComparisonDrawer from '../components/ComparisonDrawer';
import PropertyCard from '../components/PropertyCard';
import PublicPrice from '../components/PublicPrice';
import InventoryBrowse, { HomeCard } from '../pages/InventoryBrowse';

const PHONE = '(281) 324-3020';

function expectTelLinksOutsideControls(container) {
  const links = [...container.querySelectorAll('a[href^="tel:"]')];
  expect(links.length).toBeGreaterThan(0);
  for (const link of links) {
    expect(link.parentElement?.closest('a, button')).toBeNull();
  }
}

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

const oak = {
  id: '1001',
  model_name: 'The Oak',
  manufacturer: 'Champion Homes',
  classification: 'Double Wide',
  status: 'Available',
  inventory_kind: 'available_now',
  display_price: '$0',
  price_value: 90000,
  specs: { beds: 3, baths: 2, sq_ft: 1400 },
  listing_url: '/homes/1001-the-oak',
};

const pine = {
  id: '1002',
  model_name: 'The Pine',
  manufacturer: 'Champion Homes',
  classification: 'Double Wide',
  status: 'Available',
  inventory_kind: 'available_now',
  display_price: '',
  price_value: 91000,
  specs: { beds: 3, baths: 2, sq_ft: 1450 },
  listing_url: '/homes/1002-the-pine',
};

const cedar = {
  id: '1003',
  model_name: 'The Cedar',
  manufacturer: 'Champion Homes',
  classification: 'Double Wide',
  status: 'Orderable',
  inventory_kind: 'orderable_floorplan',
  is_orderable: true,
  display_price: 'Call for Price',
  price_value: 92000,
  specs: { beds: 3, baths: 2, sq_ft: 1420 },
};

describe('PublicPrice nested controls', () => {
  it('links the showroom phone only when the label is not inside another control', () => {
    const { rerender, container } = render(<PublicPrice home={{ display_price: '$0' }} />);
    expect(screen.getByRole('link', { name: PHONE })).toHaveAttribute('href', 'tel:+12813243020');
    expectTelLinksOutsideControls(container);

    rerender(<PublicPrice home={{ display_price: '$0' }} linkPhone={false} />);
    expect(screen.getByText('Call for Price')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: PHONE })).not.toBeInTheDocument();
    expect(container.querySelector('a, button')).toBeNull();

    rerender(<PublicPrice home={{ display_price: '$129,900' }} />);
    expect(screen.getByText('$129,900')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('keeps an unpriced inventory card phone link outside the listing controls', () => {
    const { container } = render(
      <HomeCard
        home={oak}
        onClick={() => {}}
        onGetPrice={() => {}}
        onApply={() => {}}
      />,
    );

    const phone = screen.getByRole('link', { name: PHONE });
    expect(phone).toHaveAttribute('href', 'tel:+12813243020');
    expect(phone.parentElement?.closest('a, button')).toBeNull();
    expect(screen.getByRole('link', { name: /View Details/i }).querySelector('a, button')).toBeNull();
    expect(container.querySelector('a[aria-label="View The Oak"]')?.querySelector('a, button')).toBeNull();
  });

  it('keeps an unpriced chat card phone link outside the card actions', () => {
    const { container } = render(
      <PropertyCard
        property={{
          id: 'chat-1',
          model_name: 'The Oak',
          manufacturer: 'Champion Homes',
          classification: 'Double Wide',
          specs: { beds: 3, baths: 2, sq_ft: 1400 },
          pricing: { display_price: '$0' },
        }}
        onToggleCompare={() => {}}
        isSelected={false}
      />,
    );

    expect(screen.getByRole('link', { name: PHONE })).toHaveAttribute('href', 'tel:+12813243020');
    expect(screen.getByRole('button', { name: /compare/i }).querySelector('a')).toBeNull();
    expect(screen.getByRole('button', { name: /view details/i }).querySelector('a')).toBeNull();
    expectTelLinksOutsideControls(container);
  });

  it('keeps an unpriced comparison price outside the remove control', () => {
    const { container } = render(
      <ComparisonDrawer
        isOpen
        properties={[oak]}
        onClose={() => {}}
        onRemove={() => {}}
      />,
    );

    const remove = screen.getByRole('button', { name: /Remove The Oak/i });
    expect(remove.querySelector('a')).toBeNull();
    expect(screen.getByRole('link', { name: PHONE })).toHaveAttribute('href', 'tel:+12813243020');
    expectTelLinksOutsideControls(container);
  });
});

describe('unpriced similar-home cards', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url) === '/api/marketing/inventory-context') {
        return response({ success: true, homes: [oak, pine, cedar] });
      }
      return response({ success: true });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, '', '/');
  });

  it('does not nest a phone link inside the similar-home link or button', async () => {
    window.history.replaceState({}, '', '/inventory?home=1001');
    render(<InventoryBrowse pathname="/inventory" />);

    const heading = await screen.findByRole('heading', { name: 'Similar Homes' });
    const section = within(heading.parentElement);

    const pineCard = section.getByRole('link', { name: /The Pine/ });
    expect(pineCard).toHaveAttribute('href', '/homes/1002-the-pine');
    expect(pineCard).toHaveTextContent('Call for Price');
    expect(pineCard.querySelector('a, button')).toBeNull();

    const cedarCard = section.getByRole('button', { name: /The Cedar/ });
    expect(cedarCard).toHaveTextContent('Call for Price');
    expect(cedarCard.querySelector('a, button')).toBeNull();

    fireEvent.click(within(cedarCard).getByText('Call for Price'));
    expect(await screen.findByRole('heading', { level: 2, name: 'The Cedar' })).toBeInTheDocument();

    expectTelLinksOutsideControls(document.body);
  });
});
