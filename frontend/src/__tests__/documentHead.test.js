import { afterEach, describe, expect, it } from 'vitest';
import { applyDocumentHead, PUBLIC_CANONICAL_ORIGIN } from '../utils/listingRoutes';

describe('applyDocumentHead', () => {
  afterEach(() => {
    document.head.innerHTML = '';
    document.title = '';
  });

  it('sets canonical, Open Graph, and Twitter tags for a public page', () => {
    applyDocumentHead({
      title: 'Manufactured Home Financing in Huffman, TX | Texas Home Outlet',
      description: 'Manufactured home financing options at Texas Home Outlet.',
      canonicalPath: '/financing',
      image: '/og-card.png',
      robots: 'index,follow',
    });

    expect(document.title).toContain('Financing');
    expect(document.querySelector('meta[name="description"]').getAttribute('content')).toContain(
      'Texas Home Outlet',
    );
    const origin = window.location.origin;
    expect(document.querySelector('link[rel="canonical"]').getAttribute('href')).toBe(
      `${origin}/financing`,
    );
    expect(document.querySelector('meta[property="og:title"]').getAttribute('content')).toContain(
      'Financing',
    );
    expect(document.querySelector('meta[property="og:image"]').getAttribute('content')).toBe(
      `${origin}/og-card.png`,
    );
    expect(document.querySelector('meta[property="og:site_name"]').getAttribute('content')).toBe(
      'Texas Home Outlet',
    );
    expect(document.querySelector('meta[name="twitter:card"]').getAttribute('content')).toBe(
      'summary_large_image',
    );
    expect(document.querySelector('meta[name="twitter:image"]').getAttribute('content')).toBe(
      `${origin}/og-card.png`,
    );
    expect(document.querySelector('meta[name="robots"]').getAttribute('content')).toBe(
      'index,follow',
    );
  });

  it('makes a relative share photo absolute and drops a 403 catalog image', () => {
    applyDocumentHead({
      title: 'TRU Magnolia',
      description: 'Call for the price.',
      canonicalPath: '/homes/30643-tru-single-section-magnolia',
      image: '/api/inventory/photos/30643/front.jpg',
      robots: 'index,follow',
    });
    const origin = window.location.origin;
    expect(document.querySelector('meta[property="og:image"]').getAttribute('content')).toBe(
      `${origin}/api/inventory/photos/30643/front.jpg`,
    );

    applyDocumentHead({
      title: 'The Absolute Value',
      description: 'Call for the price.',
      canonicalPath: '/plan/226545/solution/the-absolute-value-slc28764a/',
      image: 'https://d132mt2yijm03y.cloudfront.net/manufacturer/2025/floorplan/226545/Absolute%20Value-1.jpg',
      robots: 'index,follow',
    });
    const denied = document.querySelector('meta[property="og:image"]').getAttribute('content');
    expect(denied).toBe(`${origin}/og-card.png`);
    expect(denied).not.toContain('cloudfront.net');
    expect(denied.startsWith('https://') || denied.startsWith(origin)).toBe(true);
    expect(PUBLIC_CANONICAL_ORIGIN).toBe('https://www.texashomeoutlet.com');
  });

  it('removes canonical and og:url when robots is noindex', () => {
    applyDocumentHead({
      title: 'Home Inventory',
      description: 'Public inventory.',
      canonicalPath: '/inventory',
      robots: 'index,follow',
    });
    expect(document.querySelector('link[rel="canonical"]')).not.toBeNull();

    applyDocumentHead({
      title: 'Checkout',
      description: 'Not a public page.',
      canonicalPath: '/checkout',
      robots: 'noindex',
    });
    expect(document.querySelector('meta[name="robots"]').getAttribute('content')).toBe('noindex');
    expect(document.querySelector('link[rel="canonical"]')).toBeNull();
    expect(document.querySelector('meta[property="og:url"]')).toBeNull();
  });
});