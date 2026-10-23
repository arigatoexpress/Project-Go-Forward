import { afterEach, describe, expect, it } from 'vitest';
import { applyDocumentHead } from '../utils/listingRoutes';

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