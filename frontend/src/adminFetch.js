/**
 * Fetch wrapper for admin-only API calls.
 * Relies on httpOnly SameSite=Strict cookie for auth — no manual token handling.
 * Intercepts 401 responses to signal re-auth.
 * Retries read-only requests. Mutating requests retry only when a caller
 * explicitly marks the operation idempotent with ``retry: true``.
 */

const MAX_RETRIES = 3;
const RETRY_DELAY = 1000; // ms

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export default async function adminFetch(url, options = {}) {
  let lastError;
  const { retry, ...fetchOptions } = options;
  const method = (fetchOptions.method || 'GET').toUpperCase();
  const retryable = retry === true || (retry !== false && ['GET', 'HEAD', 'OPTIONS'].includes(method));
  const attempts = retryable ? MAX_RETRIES : 1;

  // Inject CSRF token into headers for admin endpoints
  const csrfMatch = document.cookie.match(/tho_csrf_token=([^;]+)/);
  if (csrfMatch) {
    fetchOptions.headers = {
      ...(fetchOptions.headers || {}),
      'X-CSRF-Token': csrfMatch[1],
    };
  }

  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const response = await fetch(url, fetchOptions);

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
      if (response.status >= 500 && attempt < attempts - 1) {
        lastError = new Error(`Server error: ${response.status}`);
        // Carry the status so consumers can map it via describeFetchError
        // instead of ever showing this raw message to users.
        lastError.status = response.status;
        await sleep(RETRY_DELAY * (attempt + 1)); // Exponential backoff
        continue;
      }

      // Client errors (4xx) don't retry
      return response;

    } catch (error) {
      // Network errors might be transient, retry
      lastError = error;

      if (attempt < attempts - 1) {
        console.warn(`adminFetch attempt ${attempt + 1} failed, retrying...`, error.message);
        await sleep(RETRY_DELAY * (attempt + 1));
      }
    }
  }

  // All retries exhausted
  if (retryable) {
    console.error('adminFetch failed after all retries:', lastError);
  }
  throw lastError;
}
