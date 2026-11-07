import { trackEvent } from './utils/analytics';

// Phone portrait, plus a short landscape phone. Wider screens keep the
// existing inventory bubble; this query must stay in lockstep with the
// `.tho-mobile-chat-prompt` hide rule in App.css.
export const PHONE_CHAT_MEDIA = '(max-width: 639px), (max-width: 932px) and (max-height: 500px)';

const ENTRY_KEY = 'tho_chat_open_source';
const STARTED_KEY = 'tho_chat_started';
const SHOWN_KEY = 'tho_mobile_chat_prompt_shown';
const DISMISSED_KEY = 'tho_mobile_chat_prompt_dismissed';

const TOKEN_FIELDS = ['placement', 'source', 'viewport', 'kind'];
const PATH_FIELDS = ['page_path'];
const TOKEN_RE = /^[a-z0-9_]{1,40}$/;
const PATH_RE = /^\/[\w./-]*$/;

export function isPhoneChatViewport() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(PHONE_CHAT_MEDIA).matches;
}

export function currentChatViewport() {
  return isPhoneChatViewport() ? 'phone' : 'wide';
}

function writeSession(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // Storage can be blocked. Chat still works.
  }
}

function readSession(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function markChatEntry(source) {
  if (!TOKEN_RE.test(String(source || ''))) return;
  writeSession(ENTRY_KEY, source);
}

export function consumeChatEntry() {
  const value = readSession(ENTRY_KEY);
  try {
    sessionStorage.removeItem(ENTRY_KEY);
  } catch {
    // Ignore a blocked storage API.
  }
  return TOKEN_RE.test(String(value || '')) ? value : 'direct';
}

export function markChatStarted() {
  writeSession(STARTED_KEY, '1');
}

export function chatAlreadyStarted() {
  return readSession(STARTED_KEY) === '1';
}

export function promptAlreadyShown() {
  return readSession(SHOWN_KEY) === '1';
}

export function markPromptShown() {
  writeSession(SHOWN_KEY, '1');
}

export function promptDismissed() {
  return readSession(DISMISSED_KEY) === '1';
}

export function markPromptDismissed() {
  writeSession(DISMISSED_KEY, '1');
}

/**
 * First-party chat events only. Message text, contact fields, and secrets
 * are dropped before the analytics beacon is built.
 */
export function trackChatEvent(event, data = {}) {
  const safe = {};
  for (const key of TOKEN_FIELDS) {
    const value = data[key];
    if (typeof value === 'string' && TOKEN_RE.test(value)) safe[key] = value;
  }
  for (const key of PATH_FIELDS) {
    const value = data[key];
    if (typeof value === 'string' && PATH_RE.test(value)) safe[key] = value.slice(0, 200);
  }
  if (data.message_length != null && typeof data.message_length !== 'boolean') {
    const number = Number(data.message_length);
    if (Number.isInteger(number) && number >= 0 && number <= 8000) {
      safe.message_length = number;
    }
  }
  trackEvent(event, safe);
}

export function recordChatSend({ kind, text }) {
  const length = typeof text === 'string' ? text.length : 0;
  const viewport = currentChatViewport();
  const page_path = '/chat';
  if (!chatAlreadyStarted()) {
    markChatStarted();
    trackChatEvent('chat_started', { source: kind, viewport, page_path });
  }
  trackChatEvent('chat_message_sent', {
    kind,
    source: kind,
    viewport,
    page_path,
    message_length: length,
  });
}
