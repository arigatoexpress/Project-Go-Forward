/**
 * Image optimization utilities for CloudFront-hosted images.
 *
 * The backend serves images from CloudFront (https://d132mt2yijm03y.cloudfront.net).
 * While the CDN currently serves JPEGs without dynamic width/format transformation,
 * these helpers generate forward-looking srcset/sizes attributes. If the CDN
 * ever adds support for width params, the browser will automatically pick the
 * optimal resolution.
 */

const DEFAULT_WIDTHS = [400, 800, 1200];

/**
 * Generate a srcset string with width descriptors for a given image URL.
 * Appends ?w=<width> query params for future CDN support.
 *
 * @param {string} url - The original image URL.
 * @param {number[]} [widths=[400, 800, 1200]] - Width descriptors to generate.
 * @returns {string} A srcset string like "url?w=400 400w, url?w=800 800w, ...".
 */
export function generateSrcSet(url, widths = DEFAULT_WIDTHS) {
  if (!url) return '';
  const separator = url.includes('?') ? '&' : '?';
  return widths
    .map((w) => `${url}${separator}w=${w} ${w}w`)
    .join(', ');
}

/**
 * Return an appropriate `sizes` attribute based on the container context.
 *
 * @param {string} container - A token describing the image's UI container.
 * @returns {string} A valid `sizes` attribute value.
 */
export function getImageSizes(container) {
  switch (container) {
    case 'card-grid':
      // Cards in a responsive grid (minmax(320px, 1fr))
      return '(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 400px';
    case 'card-hero':
      // PropertyCard hero image (max-w-2xl single card, often full width)
      return '(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 400px';
    case 'modal':
      // Full-width modal image, up to 4xl container
      return '(max-width: 768px) 100vw, 800px';
    case 'modal-thumbnail':
      // Small thumbnails in modal
      return '80px';
    case 'similar-homes':
      // Similar homes grid (minmax(200px, 1fr))
      return '(max-width: 640px) 50vw, 200px';
    case 'hero-background':
      // Full-width hero background
      return '100vw';
    case 'featured-spotlight':
      // Featured home spotlight (roughly half the screen on desktop)
      return '(max-width: 1024px) 100vw, 50vw';
    case 'document-center-card':
      // Small card images in document center
      return '(max-width: 640px) 100vw, 300px';
    case 'analytics-table':
      // Tiny thumbnail in a table row
      return '40px';
    default:
      return '100vw';
  }
}
