/** localStorage key for the public chat session id. The owner secret is an httpOnly cookie. */
export const CHAT_SESSION_STORAGE_KEY = 'tho_session_id';

function normalizeMessages(data) {
  if (!data || !Array.isArray(data.messages)) return [];
  return data.messages
    .filter((msg) => msg && (msg.role === 'user' || msg.role === 'model') && typeof msg.text === 'string')
    .map((msg) => ({ role: msg.role, text: msg.text }));
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

/**
 * Attach this browser to a chat session.
 *
 * A stored id is kept only when the owner cookie is accepted. A 404 means the
 * cookie is missing or belongs to a different session, including chats started
 * before owner cookies existed. Those start over so the visitor sees a fresh
 * greeting instead of an error.
 */
export async function resolveChatSession({
  storage = globalThis.localStorage,
  fetchImpl = globalThis.fetch.bind(globalThis),
} = {}) {
  const stored = storage?.getItem(CHAT_SESSION_STORAGE_KEY) || '';
  if (stored) {
    try {
      const response = await fetchImpl(`/api/chat/session/${encodeURIComponent(stored)}`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (response.ok) {
        return {
          sessionId: stored,
          messages: normalizeMessages(await readJson(response)),
          fresh: false,
        };
      }
      if (response.status !== 404) {
        return { sessionId: stored, messages: [], fresh: false };
      }
      try {
        storage.removeItem(CHAT_SESSION_STORAGE_KEY);
      } catch {
        // Still mint a replacement below.
      }
    } catch {
      return { sessionId: stored, messages: [], fresh: false };
    }
  }

  try {
    const response = await fetchImpl('/api/chat/session', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    const data = await readJson(response);
    const sessionId = response.ok && typeof data.session_id === 'string' ? data.session_id : '';
    if (sessionId) {
      try {
        storage.setItem(CHAT_SESSION_STORAGE_KEY, sessionId);
      } catch {
        // This page can still use the id. A later reload may start fresh.
      }
      return { sessionId, messages: [], fresh: true };
    }
  } catch {
    // The greeting still renders. The next send tries again.
  }
  return { sessionId: '', messages: [], fresh: true };
}

/**
 * Use the session id POST /run actually wrote to.
 *
 * A different id means this browser did not own the id it sent. The page
 * switches to the fresh chat the server started.
 */
export function adoptRunSessionId(currentId, payload, storage = globalThis.localStorage) {
  const returned = payload && typeof payload.session_id === 'string' ? payload.session_id.trim() : '';
  if (!returned) return currentId || '';
  if (returned !== currentId) {
    try {
      storage.setItem(CHAT_SESSION_STORAGE_KEY, returned);
    } catch {
      // This page can still switch for this visit. A reload may start fresh.
    }
  }
  return returned;
}
