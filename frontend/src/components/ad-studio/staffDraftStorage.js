import { isComposerStarted, toComposerDraft } from './composerDraft';

/** Browser-only Ad Studio drafts. Scoped to the signed-in staff session. */

const STORAGE_PREFIX = 'tho-ad-studio-drafts';
const COMPOSER_PREFIX = 'tho-ad-studio-composer';

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

export function readStaffSessionScope(doc = globalThis.document) {
  // The readable staff session cookie is the identifier already on the client.
  // It changes when someone else signs in. The saved work email is not used:
  // it can be typed before sign-in and can change during one session.
  const csrf = readCookie(doc?.cookie, 'tho_csrf_token');
  if (!csrf) return '';
  return hashText(csrf);
}

function storageKey(scope) {
  return `${STORAGE_PREFIX}:${scope}`;
}

function composerKey(scope) {
  return `${COMPOSER_PREFIX}:${scope}`;
}

function removePrefixedKeys(storage, prefix, except = '') {
  const keys = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (key && key.startsWith(`${prefix}:`) && key !== except) keys.push(key);
  }
  keys.forEach(key => storage.removeItem(key));
}

function removeDraftKeys(storage, except = '') {
  removePrefixedKeys(storage, STORAGE_PREFIX, except);
}

function removeComposerKeys(storage, except = '') {
  removePrefixedKeys(storage, COMPOSER_PREFIX, except);
}

export function readStaffDrafts(
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc);
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
  const scope = readStaffSessionScope(doc);
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

export function readComposerDraft(
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc);
  if (!scope || !storage) return null;
  try {
    const raw = storage.getItem(composerKey(scope));
    if (!raw) return null;
    const draft = toComposerDraft(JSON.parse(raw));
    return isComposerStarted(draft) ? draft : null;
  } catch {
    return null;
  }
}

export function writeComposerDraft(
  draft,
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc);
  if (!scope || !storage) return;
  try {
    const key = composerKey(scope);
    const clean = toComposerDraft(draft);
    removeComposerKeys(storage, key);
    if (!isComposerStarted(clean)) {
      storage.removeItem(key);
      return;
    }
    storage.setItem(key, JSON.stringify(clean));
  } catch {
    // Private browsing can reject storage. The form still works until refresh.
  }
}

export function clearComposerDraft(
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc);
  if (!scope || !storage) return;
  try {
    storage.removeItem(composerKey(scope));
  } catch {
    // Ignore storage failures. Cancel still clears the screen.
  }
}

export function keepOnlyCurrentStaffDrafts(
  storage = globalThis.localStorage,
  doc = globalThis.document,
) {
  const scope = readStaffSessionScope(doc);
  if (!scope || !storage) return;
  try {
    removeDraftKeys(storage, storageKey(scope));
    removeComposerKeys(storage, composerKey(scope));
  } catch {
    // Ignore storage failures. Sign-out still clears what it can.
  }
}

export function clearStaffDrafts(storage = globalThis.localStorage) {
  if (!storage) return;
  try {
    removeDraftKeys(storage);
    removeComposerKeys(storage);
  } catch {
    // Ignore storage failures. Sign-out still leaves the screen.
  }
}
