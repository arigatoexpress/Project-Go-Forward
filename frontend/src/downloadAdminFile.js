import adminFetch from './adminFetch';
import { responseErrorMessage } from './utils/apiError.js';

function filenameFromDisposition(disposition) {
  if (!disposition) return '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || '';
}

/**
 * Download an admin-protected file (cookie auth via adminFetch).
 *
 * Returns the resolved filename on success so the UI can confirm to the user
 * exactly what landed in their Downloads folder.
 *
 * Robustness notes (these are why this is not a one-liner):
 *  - Cleanup (revokeObjectURL + anchor removal) is DEFERRED. Revoking the object
 *    URL synchronously after click() cancels in-progress downloads of large
 *    files in Chrome/Firefox/Safari — the browser has not finished reading the
 *    blob yet. Small files win the race and download fine; large merged packets
 *    silently fail with no error. Deferring cleanup fixes the "Download All
 *    Documents button does nothing" bug.
 *  - An empty (0-byte) body is treated as a failure with a plain-language
 *    message instead of silently saving a broken file.
 */
export default async function downloadAdminFile(url, fallbackFilename = 'document.pdf') {
  const response = await adminFetch(url);
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error(
        'Your admin session expired before the document could download. Re-enter the admin PIN, then click Download again.',
      );
    }
    throw new Error(await responseErrorMessage(response, { context: 'download the document' }));
  }

  const blob = await response.blob();
  if (!blob || blob.size === 0) {
    throw new Error(
      'The document came back empty. Please go back, re-generate, and try the download again.',
    );
  }

  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) ||
    fallbackFilename ||
    url.split('/').pop() ||
    'document.pdf';

  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  link.rel = 'noopener';
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();

  // Defer cleanup so the browser can finish reading the blob before the object
  // URL is revoked. Revoking too early cancels large downloads (see note above).
  setTimeout(() => {
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }, 10000);

  return filename;
}
