/**
 * Fetch wrapper that attaches the admin token from sessionStorage.
 * Drop-in replacement for fetch() on admin-only API calls.
 * Intercepts 401 responses to clear stale tokens and signal re-auth.
 */
export default async function adminFetch(url, options = {}) {
  const token = sessionStorage.getItem('tho_admin_token');
  const headers = { ...options.headers };
  if (token) {
    headers['X-Admin-Token'] = token;
  }
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    sessionStorage.removeItem('tho_admin_token');
    window.dispatchEvent(new CustomEvent('admin-session-expired'));
  }
  return response;
}
