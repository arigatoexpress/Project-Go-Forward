import { describe, it, expect, afterEach } from 'vitest';
import {
  GENERIC_ERROR_MESSAGE,
  NETWORK_ERROR_MESSAGE,
  OFFLINE_ERROR_MESSAGE,
  extractErrorMessage,
  isTechnicalMessage,
  safeUserMessage,
  friendlyStatusMessage,
  describeFetchError,
  responseErrorMessage,
} from '../utils/apiError.js';

function setOnline(online) {
  Object.defineProperty(window.navigator, 'onLine', {
    value: online,
    configurable: true,
  });
}

afterEach(() => setOnline(true));

describe('extractErrorMessage', () => {
  it('returns null for non-object bodies', () => {
    expect(extractErrorMessage(null)).toBeNull();
    expect(extractErrorMessage(undefined)).toBeNull();
    expect(extractErrorMessage('error')).toBeNull();
    expect(extractErrorMessage(42)).toBeNull();
  });

  it('reads flat {error} shape', () => {
    expect(extractErrorMessage({ error: 'No homes found' })).toBe('No homes found');
  });

  it('reads global envelope {success:false, message} shape', () => {
    expect(extractErrorMessage({ success: false, status_code: 400, message: 'Invalid request payload.' }))
      .toBe('Invalid request payload.');
  });

  it('reads {success:false, error} mixed shape', () => {
    expect(extractErrorMessage({ success: false, error: 'PIN incorrect' })).toBe('PIN incorrect');
  });

  it('prefers error over message when both present', () => {
    expect(extractErrorMessage({ error: 'specific', message: 'generic' })).toBe('specific');
  });

  it('reads string {detail} (FastAPI default)', () => {
    expect(extractErrorMessage({ detail: 'Not authenticated' })).toBe('Not authenticated');
  });

  it('flattens FastAPI validation detail arrays', () => {
    const body = { detail: [{ msg: 'field required' }, { msg: 'value too short' }] };
    expect(extractErrorMessage(body)).toBe('field required; value too short');
  });

  it('reads nested {error:{message}} shape', () => {
    expect(extractErrorMessage({ error: { message: 'nested fail' } })).toBe('nested fail');
  });

  it('returns null for empty/blank messages', () => {
    expect(extractErrorMessage({ error: '   ' })).toBeNull();
    expect(extractErrorMessage({})).toBeNull();
    expect(extractErrorMessage({ detail: [] })).toBeNull();
  });
});

describe('isTechnicalMessage', () => {
  it('flags browser network errors', () => {
    expect(isTechnicalMessage('Failed to fetch')).toBe(true);
    expect(isTechnicalMessage('NetworkError when attempting to fetch resource.')).toBe(true);
    expect(isTechnicalMessage('Load failed')).toBe(true);
  });

  it('flags JS/Python exception strings', () => {
    expect(isTechnicalMessage('TypeError: Cannot read properties of undefined')).toBe(true);
    expect(isTechnicalMessage("KeyError: 'customer_id'")).toBe(true);
    expect(isTechnicalMessage("'NoneType' object has no attribute 'get'")).toBe(true);
    expect(isTechnicalMessage('Traceback (most recent call last): File "main.py", line 5')).toBe(true);
  });

  it('flags HTTP status strings and statusText leaks', () => {
    expect(isTechnicalMessage('HTTP 500: Internal Server Error')).toBe(true);
    expect(isTechnicalMessage('500 Internal Server Error')).toBe(true);
    expect(isTechnicalMessage('Download failed: statusText=Bad Gateway')).toBe(true);
  });

  it('flags raw JSON dumps', () => {
    expect(isTechnicalMessage('{"error":"boom"}')).toBe(true);
    expect(isTechnicalMessage('[{"loc":["body"],"msg":"x"}]')).toBe(true);
  });

  it('passes friendly copy through', () => {
    expect(isTechnicalMessage('No homes match your search.')).toBe(false);
    expect(isTechnicalMessage('Please enter a valid 10-digit phone number.')).toBe(false);
    expect(isTechnicalMessage('Something went wrong on our end. Please try again.')).toBe(false);
    expect(isTechnicalMessage('')).toBe(false);
    expect(isTechnicalMessage(null)).toBe(false);
    expect(isTechnicalMessage(undefined)).toBe(false);
  });
});

describe('safeUserMessage', () => {
  it('keeps friendly backend messages', () => {
    expect(safeUserMessage('Please include a phone number.')).toBe('Please include a phone number.');
  });

  it('replaces technical messages with the fallback', () => {
    expect(safeUserMessage('TypeError: Failed to fetch')).toBe(GENERIC_ERROR_MESSAGE);
    expect(safeUserMessage('HTTP 500: Internal Server Error', 'Custom fallback')).toBe('Custom fallback');
  });

  it('uses fallback for empty input', () => {
    expect(safeUserMessage('')).toBe(GENERIC_ERROR_MESSAGE);
    expect(safeUserMessage(null)).toBe(GENERIC_ERROR_MESSAGE);
  });
});

describe('friendlyStatusMessage', () => {
  it('maps common statuses to plain language', () => {
    expect(friendlyStatusMessage(400)).toMatch(/check your input/i);
    expect(friendlyStatusMessage(401)).toMatch(/session has expired/i);
    expect(friendlyStatusMessage(403)).toMatch(/permission/i);
    expect(friendlyStatusMessage(404)).toMatch(/couldn’t find/i);
    expect(friendlyStatusMessage(408)).toMatch(/timed out/i);
    expect(friendlyStatusMessage(409)).toMatch(/conflicts/i);
    expect(friendlyStatusMessage(413)).toMatch(/too large/i);
    expect(friendlyStatusMessage(422)).toMatch(/invalid/i);
    expect(friendlyStatusMessage(500)).toBe(GENERIC_ERROR_MESSAGE);
    expect(friendlyStatusMessage(503)).toBe(GENERIC_ERROR_MESSAGE);
  });

  it('uses context for 5xx/unknown statuses', () => {
    expect(friendlyStatusMessage(500, { context: 'load inventory' }))
      .toBe('We couldn’t load inventory right now. Please try again.');
    expect(friendlyStatusMessage(0, { context: 'send your message' }))
      .toBe('We couldn’t send your message right now. Please try again.');
  });

  it('personalizes 429 with Retry-After seconds', () => {
    expect(friendlyStatusMessage(429, { retryAfter: '30' }))
      .toBe('Too many attempts. Please wait about 30 seconds and try again.');
    expect(friendlyStatusMessage(429, { retryAfter: 1 }))
      .toBe('Too many attempts. Please wait about 1 second and try again.');
    expect(friendlyStatusMessage(429)).toMatch(/wait a moment/i);
  });
});

describe('describeFetchError', () => {
  it('translates browser network failures', () => {
    expect(describeFetchError(new TypeError('Failed to fetch'))).toBe(NETWORK_ERROR_MESSAGE);
    expect(describeFetchError(new Error('Load failed'))).toBe(NETWORK_ERROR_MESSAGE);
  });

  it('reports offline state when navigator says so', () => {
    setOnline(false);
    expect(describeFetchError(new TypeError('Failed to fetch'))).toBe(OFFLINE_ERROR_MESSAGE);
  });

  it('uses err.status when present', () => {
    const err = new Error('boom');
    err.status = 503;
    expect(describeFetchError(err, 'load leads')).toBe('We couldn’t load leads right now. Please try again.');
  });

  it('parses status out of legacy "HTTP 500" style messages', () => {
    expect(describeFetchError(new Error('HTTP 429: Too Many Requests'))).toMatch(/Too many attempts/i);
    expect(describeFetchError(new Error('HTTP 500: Internal Server Error'), 'save the deal'))
      .toBe('We couldn’t save the deal right now. Please try again.');
  });

  it('handles AbortError', () => {
    const err = new Error('aborted');
    err.name = 'AbortError';
    expect(describeFetchError(err)).toMatch(/took too long/i);
  });

  it('falls back to generic/context copy for unknown errors and never leaks raw text', () => {
    expect(describeFetchError(new Error("KeyError: 'xyz'"), 'load homes'))
      .toBe('We couldn’t load homes right now. Please try again.');
    expect(describeFetchError('weird string error')).toBe(GENERIC_ERROR_MESSAGE);
    expect(describeFetchError(undefined)).toBe(GENERIC_ERROR_MESSAGE);
  });
});

describe('responseErrorMessage', () => {
  function mockResponse(status, body, headers = {}) {
    return {
      status,
      clone() { return this; },
      json: async () => {
        if (body instanceof Error) throw body;
        return body;
      },
      headers: { get: (k) => headers[k] ?? null },
    };
  }

  it('prefers sanitized backend messages', async () => {
    const res = mockResponse(400, { error: 'Please enter a valid email address.' });
    expect(await responseErrorMessage(res)).toBe('Please enter a valid email address.');
  });

  it('masks technical backend messages with status-based copy', async () => {
    const res = mockResponse(500, { error: "KeyError: 'customer_id'" });
    expect(await responseErrorMessage(res, { context: 'generate documents' }))
      .toBe('We couldn’t generate documents right now. Please try again.');
  });

  it('falls back to status copy when the body is not JSON', async () => {
    const res = mockResponse(503, new Error('not json'));
    expect(await responseErrorMessage(res)).toBe(GENERIC_ERROR_MESSAGE);
  });

  it('honors the Retry-After header on 429', async () => {
    const res = mockResponse(429, null, { 'Retry-After': '45' });
    expect(await responseErrorMessage(res)).toMatch(/45 seconds/);
  });

  it('uses a pre-parsed body when provided', async () => {
    const res = mockResponse(400, new Error('should not be called'));
    expect(await responseErrorMessage(res, { body: { detail: 'Bad field' } })).toBe('Bad field');
  });
});
