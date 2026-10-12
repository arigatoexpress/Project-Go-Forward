import { describe, expect, it, vi } from 'vitest';
import { CHAT_SESSION_STORAGE_KEY, adoptRunSessionId, resolveChatSession } from '../chatSession';

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (key) => (Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null),
    setItem: (key, value) => {
      data[key] = String(value);
    },
    removeItem: (key) => {
      delete data[key];
    },
  };
}

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

describe('resolveChatSession', () => {
  it('keeps a stored session and its history when this browser is the owner', async () => {
    const storage = memoryStorage({ [CHAT_SESSION_STORAGE_KEY]: 'session-a' });
    const fetchImpl = vi.fn(async () => jsonResponse({
      success: true,
      messages: [
        { role: 'user', text: 'Looking for a 3 bed' },
        { role: 'model', text: 'I can help with that.' },
        { role: 'system', text: 'drop me' },
        { role: 'user', text: 12 },
      ],
    }));

    const result = await resolveChatSession({ storage, fetchImpl });

    expect(result).toEqual({
      sessionId: 'session-a',
      fresh: false,
      messages: [
        { role: 'user', text: 'Looking for a 3 bed' },
        { role: 'model', text: 'I can help with that.' },
      ],
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/chat/session/session-a',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('starts a fresh chat when the history read is rejected', async () => {
    const storage = memoryStorage({ [CHAT_SESSION_STORAGE_KEY]: 'old-browser-session' });
    const fetchImpl = vi.fn(async (url, init) => {
      if (String(url).startsWith('/api/chat/session/')) {
        return jsonResponse({ success: false, error: 'Not found' }, { ok: false, status: 404 });
      }
      expect(init).toEqual(expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
      }));
      return jsonResponse({ success: true, session_id: 'fresh-session' });
    });

    const result = await resolveChatSession({ storage, fetchImpl });

    expect(result).toEqual({ sessionId: 'fresh-session', messages: [], fresh: true });
    expect(storage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe('fresh-session');
  });

  it('keeps the stored id when history cannot be loaded', async () => {
    const storage = memoryStorage({ [CHAT_SESSION_STORAGE_KEY]: 'session-a' });
    const fetchImpl = vi.fn(async () => {
      throw new Error('offline');
    });

    await expect(resolveChatSession({ storage, fetchImpl })).resolves.toEqual({
      sessionId: 'session-a',
      messages: [],
      fresh: false,
    });
    expect(storage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe('session-a');
  });

  it('mints a session for a first visit', async () => {
    const storage = memoryStorage();
    const fetchImpl = vi.fn(async () => jsonResponse({ success: true, session_id: 'brand-new' }));

    const result = await resolveChatSession({ storage, fetchImpl });

    expect(result.sessionId).toBe('brand-new');
    expect(result.fresh).toBe(true);
    expect(storage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe('brand-new');
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/chat/session',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    );
  });
});

describe('adoptRunSessionId', () => {
  it('switches the page to a replacement id returned by /run', () => {
    const storage = memoryStorage({ [CHAT_SESSION_STORAGE_KEY]: 'victim-session' });

    const next = adoptRunSessionId(
      'victim-session',
      { text: 'Hello from a new chat.', session_id: 'fresh-session' },
      storage,
    );

    expect(next).toBe('fresh-session');
    expect(storage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe('fresh-session');
  });

  it('keeps the current id when /run continues that chat', () => {
    const storage = memoryStorage({ [CHAT_SESSION_STORAGE_KEY]: 'session-a' });

    const next = adoptRunSessionId(
      'session-a',
      { text: 'Still here.', session_id: 'session-a' },
      storage,
    );

    expect(next).toBe('session-a');
    expect(storage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe('session-a');
  });

  it('keeps the current id when the reply has no session id', () => {
    const storage = memoryStorage({ [CHAT_SESSION_STORAGE_KEY]: 'session-a' });

    expect(adoptRunSessionId('session-a', { text: 'Hello' }, storage)).toBe('session-a');
    expect(storage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe('session-a');
  });
});
