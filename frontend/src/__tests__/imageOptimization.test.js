import { describe, it, expect } from 'vitest';
import { generateSrcSet, getImageSizes, supportsWidthVariants } from '../utils/imageOptimization';

describe('generateSrcSet', () => {
  it('returns empty string for falsy input', () => {
    expect(generateSrcSet('')).toBe('');
    expect(generateSrcSet(null)).toBe('');
    expect(generateSrcSet(undefined)).toBe('');
  });

  it('does not invent width variants the CDN would ignore', () => {
    const url = 'https://d132mt2yijm03y.cloudfront.net/photo.jpg';
    expect(supportsWidthVariants(url)).toBe(false);
    expect(generateSrcSet(url)).toBe('');
    expect(generateSrcSet('https://example.com/photo.jpg?token=abc')).toBe('');
    expect(generateSrcSet('https://example.com/photo.jpg', [200, 600])).toBe('');
  });
});

describe('getImageSizes', () => {
  it('returns card-grid sizes', () => {
    expect(getImageSizes('card-grid')).toBe(
      '(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 400px'
    );
  });

  it('returns modal sizes', () => {
    expect(getImageSizes('modal')).toBe('(max-width: 768px) 100vw, 800px');
  });

  it('returns modal-thumbnail sizes', () => {
    expect(getImageSizes('modal-thumbnail')).toBe('80px');
  });

  it('returns hero-background sizes', () => {
    expect(getImageSizes('hero-background')).toBe('100vw');
  });

  it('returns listing-page sizes', () => {
    expect(getImageSizes('listing-page')).toBe('(max-width: 640px) 100vw, 640px');
  });

  it('falls back to 100vw for unknown container', () => {
    expect(getImageSizes('unknown')).toBe('100vw');
  });
});
