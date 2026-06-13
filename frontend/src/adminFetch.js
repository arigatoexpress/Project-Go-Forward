/**
 * Fetch wrapper for admin-only API calls.
 * Relies on httpOnly SameSite=Strict cookie for auth — no manual token handling.
 * Intercepts 401 responses to signal re-auth.
 * Includes automatic retry logic for transient failures.
 */

const MAX_RETRIES = 3;
const RETRY_DELAY = 1000; // ms

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export default async function adminFetch(url, options = {}) {
  let lastError;

  // Inject CSRF token into headers for admin endpoints
  const csrfMatch = document.cookie.match(/tho_csrf_token=([^;]+)/);
  if (csrfMatch) {
    options.headers = {
      ...(options.headers || {}),
      'X-CSRF-Token': csrfMatch[1],
    };
  }

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const response = await fetch(url, options);

      // Handle auth errors immediately (no retry)
      if (response.status === 401) {
        window.dispatchEvent(new CustomEvent('admin-session-expired'));
        return response;
      }

      // Return successful responses
      if (response.ok) {
        return response;
      }

      // Server errors (5xx) might be transient, retry
      if (response.status >= 500 && attempt < MAX_RETRIES - 1) {
        lastError = new Error(`Server error: ${response.status}`);
        await sleep(RETRY_DELAY * (attempt + 1)); // Exponential backoff
        continue;
      }

      // Client errors (4xx) don't retry
      return response;

    } catch (error) {
      // Network errors might be transient, retry
      lastError = error;

      if (attempt < MAX_RETRIES - 1) {
        console.warn(`adminFetch attempt ${attempt + 1} failed, retrying...`, error.message);
        await sleep(RETRY_DELAY * (attempt + 1));
      }
    }
  }

  // All retries exhausted
  console.error('adminFetch failed after all retries:', lastError);
  throw lastError;
}
