/**
 * Fetch wrapper that attaches the admin token from sessionStorage.
 * Drop-in replacement for fetch() on admin-only API calls.
 */
export default function adminFetch(url, options = {}) {
  const token = sessionStorage.getItem('tho_admin_token') || '';
  const headers = {
    ...options.headers,
    'X-Admin-Token': token,
  };
  return fetch(url, { ...options, headers });
}
