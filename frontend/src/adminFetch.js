/**
 * Fetch wrapper that attaches the admin token from sessionStorage.
 * Drop-in replacement for fetch() on admin-only API calls.
 * Intercepts 401 responses to clear stale tokens and signal re-auth.
 * Includes automatic retry logic for transient failures.
 */

const MAX_RETRIES = 3;
const RETRY_DELAY = 1000; // ms

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export default async function adminFetch(url, options = {}) {
  const token = sessionStorage.getItem('tho_admin_token');
  const headers = { ...options.headers };
  if (token) {
    headers['X-Admin-Token'] = token;
  }

  let lastError;
  
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const response = await fetch(url, { ...options, headers });
      
      // Handle auth errors immediately (no retry)
      if (response.status === 401) {
        sessionStorage.removeItem('tho_admin_token');
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
