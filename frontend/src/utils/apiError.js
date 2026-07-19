/**
 * Centralized user-facing error message utilities.
 *
 * The backend returns several error shapes depending on the endpoint:
 *   - Global handlers:  {"success": false, "status_code": 400, "message": "..."}
 *   - Ad-hoc endpoints: {"error": "..."} (sometimes with HTTP 200)
 *   - Mixed:            {"success": false, "error": "..."}
 *   - FastAPI default:  {"detail": "..."} or {"detail": [{"msg": "..."}, ...]}
 *
 * Callers used to hand-roll `data.error || fallback` and leak raw technical
 * strings ("TypeError: Failed to fetch", "HTTP 500: Internal Server Error",
 * str(exception) text) into the UI. Every user-visible error path should go
 * through these helpers so customers never see raw technical text.
 */

export const GENERIC_ERROR_MESSAGE =
  'Something went wrong on our end. Please try again.';
export const NETWORK_ERROR_MESSAGE =
  'We couldn’t reach the server. Check your connection and try again.';
export const OFFLINE_ERROR_MESSAGE =
  'You appear to be offline. Check your connection and try again.';

/**
 * Extract the most specific human-readable message from a parsed error body.
 * Returns null when the body carries no usable message. Does NOT sanitize —
 * pass the result through safeUserMessage() before showing it to users.
 *
 * @param {*} body Parsed JSON response body (any type).
 * @returns {string|null}
 */
export function extractErrorMessage(body) {
  if (!body || typeof body !== 'object') return null;

  const candidates = [];

  // {"error": "..."} or {"error": {"message": "..."}}
  if (typeof body.error === 'string') candidates.push(body.error);
  else if (body.error && typeof body.error === 'object') {
    if (typeof body.error.message === 'string') candidates.push(body.error.message);
  }

  // {"message": "..."} (global envelope)
  if (typeof body.message === 'string') candidates.push(body.message);

  // {"detail": "..."} or FastAPI validation array [{"msg": "..."}, ...]
  if (typeof body.detail === 'string') candidates.push(body.detail);
  else if (Array.isArray(body.detail)) {
    const parts = body.detail
      .map(item => (item && typeof item.msg === 'string' ? item.msg : null))
      .filter(Boolean);
    if (parts.length) candidates.push(parts.join('; '));
  }

  const found = candidates.map(s => s.trim()).find(s => s.length > 0);
  return found || null;
}

/**
 * Heuristic: does this string look like raw technical output that should
 * never be shown to an end user?
 * @param {*} text
 * @returns {boolean}
 */
export function isTechnicalMessage(text) {
  if (typeof text !== 'string' || !text.trim()) return false;
  const t = text.trim();
  return (
    /failed to fetch/i.test(t) ||
    /network ?error/i.test(t) ||
    /load failed/i.test(t) || // Safari's network failure string
    /\b(TypeError|ReferenceError|SyntaxError|RangeError|EvalError|URIError)\b\s*:/.test(t) ||
    /\b(KeyError|ValueError|AttributeError|IndexError|RuntimeError|ConnectionError|TimeoutError|NoneType)\b/.test(t) ||
    /Traceback \(most recent call last\)/.test(t) ||
    /File "[^"]+", line \d+/.test(t) ||
    /\bHTTP\s+\d{3}\b/.test(t) ||
    /\b\d{3}\s+(Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout|Bad Request|Not Found|Forbidden|Unauthorized)\b/.test(t) ||
    /status(Text)?\s*[:=]/i.test(t) ||
    /^\s*[[{].*[\]}]\s*$/s.test(t) // raw JSON/blob dump
  );
}

/**
 * Return a backend-supplied message if it is safe for end users, otherwise
 * the fallback. Use wherever the UI would otherwise render `data.error`
 * verbatim.
 * @param {*} message Candidate message (usually from extractErrorMessage).
 * @param {string} [fallback]
 * @returns {string}
 */
export function safeUserMessage(message, fallback = GENERIC_ERROR_MESSAGE) {
  if (typeof message === 'string' && message.trim() && !isTechnicalMessage(message)) {
    return message.trim();
  }
  return fallback;
}

/**
 * Map an HTTP status code to plain-language copy for end users.
 * @param {number} status
 * @param {{context?: string, retryAfter?: number|string}} [opts]
 *   context: verb phrase, e.g. "load inventory" — used for 5xx/timeout/generic.
 *   retryAfter: seconds (from Retry-After header) — personalizes 429.
 * @returns {string}
 */
export function friendlyStatusMessage(status, opts = {}) {
  const { context, retryAfter } = opts;
  const withContext = context
    ? `We couldn’t ${context} right now. Please try again.`
    : GENERIC_ERROR_MESSAGE;

  switch (status) {
    case 400:
      return 'We couldn’t process that request. Please check your input and try again.';
    case 401:
      return 'Your session has expired. Please sign in again.';
    case 403:
      return 'You don’t have permission to do that.';
    case 404:
      return 'We couldn’t find what you were looking for.';
    case 408:
    case 504:
      return 'That request timed out. Please try again.';
    case 409:
      return 'That conflicts with something already saved. Please refresh and try again.';
    case 413:
      return 'That file is too large. Please choose a smaller one.';
    case 422:
      return 'Some of the information looks invalid. Please review it and try again.';
    case 429: {
      const seconds = parseInt(retryAfter, 10);
      return Number.isFinite(seconds) && seconds > 0
        ? `Too many attempts. Please wait about ${seconds} second${seconds === 1 ? '' : 's'} and try again.`
        : 'Too many attempts. Please wait a moment and try again.';
    }
    default:
      if (status >= 500 || status === 0) return withContext;
      return GENERIC_ERROR_MESSAGE;
  }
}

function isOffline() {
  return typeof navigator !== 'undefined' && navigator.onLine === false;
}

/**
 * Convert a caught exception (from fetch or otherwise) into user-safe copy.
 * Never returns raw browser/exception text.
 * @param {*} err The caught value.
 * @param {string} [context] Verb phrase, e.g. "load inventory".
 * @returns {string}
 */
export function describeFetchError(err, context) {
  if (isOffline()) return OFFLINE_ERROR_MESSAGE;

  // Browser network failures: TypeError("Failed to fetch") / "Load failed" / NetworkError
  const msg = err && typeof err.message === 'string' ? err.message : '';
  const looksNetwork =
    (err instanceof TypeError) ||
    /failed to fetch|load failed|network ?error/i.test(msg);
  if (looksNetwork && !/\b\d{3}\b/.test(msg)) return NETWORK_ERROR_MESSAGE;

  // Errors that carry an HTTP status (e.g. thrown by fetch helpers)
  const status = typeof err?.status === 'number'
    ? err.status
    : (() => {
        const m = msg.match(/\bHTTP\s+(\d{3})\b/i) || msg.match(/\b(\d{3})\s+(?:Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout|Bad Request|Not Found|Forbidden|Unauthorized|Too Many Requests)\b/);
        return m ? parseInt(m[1], 10) : null;
      })();
  if (status) return friendlyStatusMessage(status, { context });

  if (err && err.name === 'AbortError') {
    return 'That request took too long. Please try again.';
  }

  return context
    ? `We couldn’t ${context} right now. Please try again.`
    : GENERIC_ERROR_MESSAGE;
}

/**
 * One-shot helper for fetch call sites: given a non-ok Response, produce a
 * user-safe message. Prefers a sanitized backend-provided message, then falls
 * back to status-based copy.
 * @param {Response} response
 * @param {{context?: string, body?: *}} [opts] Pass `body` if already parsed.
 * @returns {Promise<string>}
 */
export async function responseErrorMessage(response, opts = {}) {
  const { context } = opts;
  let body = opts.body;
  if (body === undefined) {
    try {
      body = await response.clone().json();
    } catch {
      body = null;
    }
  }
  const extracted = extractErrorMessage(body);
  if (extracted && !isTechnicalMessage(extracted)) {
    return extracted.trim();
  }
  const retryAfter = response.headers?.get?.('Retry-After');
  return friendlyStatusMessage(response.status, { context, retryAfter });
}
