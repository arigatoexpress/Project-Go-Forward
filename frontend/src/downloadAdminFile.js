import adminFetch from './adminFetch';

function filenameFromDisposition(disposition) {
  if (!disposition) return '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || '';
}

export default async function downloadAdminFile(url, fallbackFilename = 'document.pdf') {
  const response = await adminFetch(url);
  if (!response.ok) {
    let message = response.status === 401
      ? 'Your admin session expired before the document could download. Re-enter the admin PIN, then click Download again.'
      : `Download failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.error || body.detail || message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }

  const blob = await response.blob();
  const filename =
    filenameFromDisposition(response.headers.get('Content-Disposition')) ||
    fallbackFilename ||
    url.split('/').pop() ||
    'document.pdf';

  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
