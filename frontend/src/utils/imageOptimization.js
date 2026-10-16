/**
 * Image optimization utilities for listing photos.
 *
 * The public image host (CloudFront and staff photo URLs) serves the file as
 * stored. It does not honor a width query, so inventing `?w=` variants makes
 * the browser download the full image once per width and splits the CDN cache.
 * `generateSrcSet` stays empty until a host actually resizes.
 */

const DEFAULT_WIDTHS = [400, 800, 1200];

/**
 * Hosts that honor a `w` query and return a smaller file.
 * Empty on purpose: the current CDN does not.
 */
const RESIZABLE_HOSTS = new Set();

/**
 * @param {string} url
 * @returns {boolean}
 */
export function supportsWidthVariants(url) {
  if (!url) return false;
  try {
    const parsed = new URL(url, 'https://www.texashomeoutlet.com');
    return RESIZABLE_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

/**
 * Generate a srcset string with width descriptors for a given image URL.
 * Returns '' when the host would ignore the width parameter.
 *
 * @param {string} url - The original image URL.
 * @param {number[]} [widths=[400, 800, 1200]] - Width descriptors to generate.
 * @returns {string} A srcset string, or '' when variants would not be smaller.
 */
export function generateSrcSet(url, widths = DEFAULT_WIDTHS) {
  if (!url || !supportsWidthVariants(url)) return '';
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
    case 'listing-page':
      return '(max-width: 640px) 100vw, 640px';
    default:
      return '100vw';
  }
}
