/** Browser-only Ad Studio drafts. Scoped to the signed-in staff session. */

const STORAGE_PREFIX = 'tho-ad-studio-drafts';
const EMAIL_STORAGE_KEY = 'tho_passkey_email';

function readCookie(cookieString, name) {
  const match = String(cookieString || '').match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  if (!match) return '';
  try {
    return decodeURIComponent(match[1]).trim();
  } catch {
    return match[1].trim();
  }
}

function hashText(value) {
  let hash = 5381;
  const text = String(value);
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash) ^ text.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
}

export function readStaffSessionScope(
  doc = globalThis.document,
  storage = globalThis.localStorage,
) {
  const csrf = readCookie(doc?.cookie, 'tho_csrf_token');
  if (!csrf) return '';
  let email = '';
  try {
    email = String(storage?.getItem(EMAIL_STORAGE_KEY) || '').trim().toLowerCase();
  } catch {
    email = '';
  }
  const who = email || 'session';
  return `${who}:${hashText(csrf)}`;
}

function storageKey(scope) {
  return `${STORAGE_PREFIX}:${scope}`;
}

function removeDraftKeys(storage, except = '') {
  const keys = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (key && key.startsWith(`${STORAGE_PREFIX}:`) && key !== except) keys.push(key);
  }
  keys.forEach(key => storage.removeItem(key));
}

export function readStaffDrafts(
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc, storage);
  if (!scope || !storage) return [];
  try {
    const parsed = JSON.parse(storage.getItem(storageKey(scope)) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(item => item && typeof item === 'object');
  } catch {
    return [];
  }
}

export function writeStaffDrafts(
  drafts,
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc, storage);
  if (!scope || !storage) return;
  try {
    const key = storageKey(scope);
    const list = Array.isArray(drafts) ? drafts : [];
    removeDraftKeys(storage, key);
    storage.setItem(key, JSON.stringify(list));
  } catch {
    // Private browsing can reject storage. The list still works until refresh.
  }
}

export function keepOnlyCurrentStaffDrafts(
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc, storage);
  if (!scope || !storage) return;
  try {
    removeDraftKeys(storage, storageKey(scope));
  } catch {
    // Ignore storage failures. Sign-out still clears what it can.
  }
}

export function clearStaffDrafts(storage = globalThis.localStorage) {
  if (!storage) return;
  try {
    removeDraftKeys(storage);
  } catch {
    // Ignore storage failures. Sign-out still leaves the screen.
  }
}
