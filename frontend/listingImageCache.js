/**
 * Which image requests the service worker may cache.
 *
 * The worker script is served with connect-src 'self'. A CacheFirst handler
 * has to fetch() the picture, and that fetch is a connection. Manufacturer
 * and dealer CDN images are allowed by img-src, not by connect-src, so
 * intercepting them fails the image and the card paints no photo. Leave
 * those requests to the browser. Same-origin staff uploads are a connection
 * to this origin and can be cached.
 *
 * @param {URL | string} url
 * @param {{ destination?: string } | null | undefined} request
 * @returns {boolean}
 */
export function shouldCacheListingImage(url, request) {
  if (!url || request?.destination !== 'image') return false;
  let parsed = url;
  if (typeof url === 'string') {
    try {
      parsed = new URL(url, 'https://www.texashomeoutlet.com');
    } catch {
      return false;
    }
  }
  return String(parsed.pathname || '').startsWith('/api/inventory/photos/');
}
