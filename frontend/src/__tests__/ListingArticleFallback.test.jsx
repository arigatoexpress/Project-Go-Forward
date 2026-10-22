import { fireEvent, render, screen } from '@testing-library/react';
import { ListingArticle } from '../pages/InventoryBrowse';

const DEAD = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3328/floorplan/232969/1.jpg';
const LIVE = 'https://d132mt2yijm03y.cloudfront.net/manufacturer/3328/floorplan/232969/1.JPG';

const home = {
  id: '41976',
  model_name: 'The Promotional Series / The Rosewood FAC28644A',
  manufacturer: 'Jessup Housing',
  status: 'Available',
  inventory_kind: 'available_now',
  image_url: '/api/inventory/photos/41976/lot.jpg',
  real_photos: ['/api/inventory/photos/41976/lot.jpg', LIVE, DEAD],
  specs: { beds: 4, baths: 2, sq_ft: 1685 },
};

describe('ListingArticle photo fallback', () => {
  it('rewrites the dead Rosewood jpg out of the gallery', () => {
    const { container } = render(
      <ListingArticle home={home} onGetPrice={() => {}} onApply={() => {}} />,
    );
    const srcs = [...container.querySelectorAll('img')].map((img) => img.getAttribute('src'));
    expect(srcs).toContain('/api/inventory/photos/41976/lot.jpg');
    expect(srcs).toContain(LIVE);
    expect(srcs).not.toContain(DEAD);
  });

  it('hides a listing photo that fails to load', () => {
    const { container } = render(
      <ListingArticle
        home={{ ...home, real_photos: ['https://cdn.example/ok.jpg', 'https://cdn.example/dead.jpg'] }}
        onGetPrice={() => {}}
        onApply={() => {}}
      />,
    );
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(2);
    fireEvent.error(images[1]);
    expect(container.querySelectorAll('img')).toHaveLength(1);
    expect(screen.getByAltText(/The Promotional Series/i)).toBeInTheDocument();
  });
});
