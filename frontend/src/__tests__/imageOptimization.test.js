import { describe, it, expect } from 'vitest';
import { generateSrcSet, getImageSizes } from '../utils/imageOptimization';

describe('generateSrcSet', () => {
  it('returns empty string for falsy input', () => {
    expect(generateSrcSet('')).toBe('');
    expect(generateSrcSet(null)).toBe('');
    expect(generateSrcSet(undefined)).toBe('');
  });

  it('generates default width descriptors for a plain URL', () => {
    const url = 'https://d132mt2yijm03y.cloudfront.net/photo.jpg';
    const result = generateSrcSet(url);
    expect(result).toContain('?w=400 400w');
    expect(result).toContain('?w=800 800w');
    expect(result).toContain('?w=1200 1200w');
    expect(result.split(',').length).toBe(3);
  });

  it('uses ampersand separator when URL already has a query string', () => {
    const url = 'https://example.com/photo.jpg?token=abc';
    const result = generateSrcSet(url);
    expect(result).toContain('&w=400 400w');
    expect(result).not.toContain('?w=400');
  });

  it('accepts custom widths', () => {
    const url = 'https://example.com/photo.jpg';
    const result = generateSrcSet(url, [200, 600]);
    expect(result).toContain('?w=200 200w');
    expect(result).toContain('?w=600 600w');
    expect(result.split(',').length).toBe(2);
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

  it('falls back to 100vw for unknown container', () => {
    expect(getImageSizes('unknown')).toBe('100vw');
  });
});
