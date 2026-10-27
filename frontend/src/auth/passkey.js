/**
 * One WebAuthn ceremony for staff passkeys.
 *
 * Registration and sign-in both go through this module. The browser calls
 * stay hand-written so the app does not take a new dependency. Discoverable
 * (resident) keys are requested by the server so a passkey can sync and show
 * up in conditional UI.
 */

import { adminCsrfHeaders } from '../adminFetch';
import { extractErrorMessage, safeUserMessage } from '../utils/apiError';

export const PASSKEY_EMAIL_FALLBACK = 'Use the email link on this page.';
export const PASSKEY_OLD_SITE =
  'Passkeys from the old site don’t carry over. Sign in by email and add a new one.';
export const PASSKEY_UNSUPPORTED_DEVICE =
  'This device can’t save a passkey. Use the email link.';

const NUDGE_NEVER_KEY = 'tho_passkey_nudge_never';
const NUDGE_LATER_KEY = 'tho_passkey_nudge_later';

let conditionalAbort = null;

function base64urlToBuffer(value) {
  const text = String(value || '');
  const padding = '='.repeat((4 - (text.length % 4)) % 4);
  const base64 = text.replace(/-/g, '+').replace(/_/g, '/') + padding;
  const raw = atob(base64);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) buf[i] = raw.charCodeAt(i);
  return buf.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function flowError(message, name = 'Error', cause) {
  const error = new Error(message);
  error.name = name;
  if (cause) error.cause = cause;
  return error;
}

function isOldSiteServerMessage(text) {
  return /unknown credential|authentication verification failed|registration verification failed|not tied to an approved/i.test(
    String(text || ''),
  );
}

function isTimeout(err) {
  const name = err?.name || '';
  const raw = String(err?.message || '');
  return name === 'TimeoutError' || /timed out|timeout/i.test(raw);
}

export function describePasskeyError(err, { flow = 'login' } = {}) {
  const name = err?.name || '';
  const raw = String(err?.message || '');
  if (name === 'InvalidStateError') {
    return `${PASSKEY_EMAIL_FALLBACK} This passkey is already saved for Texas Home Outlet. Try signing in with it.`;
  }
  if (isTimeout(err)) {
    return `${PASSKEY_EMAIL_FALLBACK} The passkey prompt timed out.`;
  }
  if (name === 'SecurityError' || isOldSiteServerMessage(raw)) {
    return PASSKEY_OLD_SITE;
  }
  if (name === 'NotAllowedError' || name === 'AbortError') {
    return `${PASSKEY_EMAIL_FALLBACK} The passkey prompt was closed before it finished.`;
  }
  if (name === 'NotSupportedError') {
    return `${PASSKEY_EMAIL_FALLBACK} This browser can’t use a passkey.`;
  }
  if (name === 'ServerError' && raw) return raw;
  if (flow === 'register' && raw && !isOldSiteServerMessage(raw)) {
    return raw.startsWith(PASSKEY_EMAIL_FALLBACK) ? raw : `${PASSKEY_EMAIL_FALLBACK} ${raw}`;
  }
  return `${PASSKEY_EMAIL_FALLBACK} The passkey step did not finish. You can try again.`;
}

export async function detectPasskeySupport() {
  const credential = typeof window !== 'undefined' ? window.PublicKeyCredential : undefined;
  if (!credential) return { webauthn: false, platform: false, conditional: false };
  let platform = false;
  let conditional = false;
  try {
    if (typeof credential.isUserVerifyingPlatformAuthenticatorAvailable === 'function') {
      platform = Boolean(await credential.isUserVerifyingPlatformAuthenticatorAvailable());
    }
  } catch {
    platform = false;
  }
  try {
    if (typeof credential.isConditionalMediationAvailable === 'function') {
      conditional = Boolean(await credential.isConditionalMediationAvailable());
    }
  } catch {
    conditional = false;
  }
  return { webauthn: true, platform, conditional };
}

export function cancelConditionalSignIn() {
  conditionalAbort?.abort();
  conditionalAbort = null;
}

async function readJson(response) {
  return response.json().catch(() => ({}));
}

function prepareRequestOptions(options) {
  const publicKey = { ...options };
  publicKey.challenge = base64urlToBuffer(options.challenge);
  if (Array.isArray(publicKey.allowCredentials)) {
    publicKey.allowCredentials = publicKey.allowCredentials.map((item) => ({
      ...item,
      id: base64urlToBuffer(item.id),
    }));
  }
  return publicKey;
}

function prepareCreationOptions(options) {
  const publicKey = {
    ...options,
    user: options.user ? { ...options.user } : options.user,
  };
  publicKey.challenge = base64urlToBuffer(options.challenge);
  if (publicKey.user?.id) publicKey.user.id = base64urlToBuffer(publicKey.user.id);
  if (Array.isArray(publicKey.excludeCredentials)) {
    publicKey.excludeCredentials = publicKey.excludeCredentials.map((item) => ({
      ...item,
      id: base64urlToBuffer(item.id),
    }));
  }
  return publicKey;
}

function assertionToJSON(credential) {
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    response: {
      authenticatorData: bufferToBase64url(credential.response.authenticatorData),
      clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
      signature: bufferToBase64url(credential.response.signature),
      userHandle: credential.response.userHandle
        ? bufferToBase64url(credential.response.userHandle)
        : null,
    },
    type: credential.type,
  };
}

function attestationToJSON(credential) {
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    response: {
      clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
      attestationObject: bufferToBase64url(credential.response.attestationObject),
    },
    type: credential.type,
    clientExtensionResults: credential.getClientExtensionResults?.() || {},
  };
}

function serverFailure(data, fallback, flow) {
  const message = safeUserMessage(extractErrorMessage(data), fallback);
  if (isOldSiteServerMessage(message)) {
    throw flowError(PASSKEY_OLD_SITE, 'SecurityError');
  }
  if (flow === 'register' && message && !/email link/i.test(message)) {
    throw flowError(`${PASSKEY_EMAIL_FALLBACK} ${message}`, 'ServerError');
  }
  throw flowError(message, 'ServerError');
}

export async function fetchPasskeyProfile() {
  const response = await fetch('/api/admin/passkey/whoami', {
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
  });
  if (!response.ok) return null;
  return readJson(response);
}

/**
 * Sign in with a passkey.
 *
 * `mediation: 'conditional'` waits for the browser to offer a saved passkey
 * in the email field. Cancelling that wait is quiet. A button press uses the
 * normal prompt and reports a message that leads with the email link.
 */
export async function signInWithPasskey({ mediation } = {}) {
  const conditional = mediation === 'conditional';
  if (!conditional) cancelConditionalSignIn();
  const support = await detectPasskeySupport();
  if (!support.webauthn) {
    throw flowError(describePasskeyError({ name: 'NotSupportedError' }), 'NotSupportedError');
  }
  if (conditional && !support.conditional) return null;

  const signalController = conditional ? new AbortController() : null;
  if (conditional) {
    conditionalAbort = signalController;
  }

  const beginRes = await fetch('/api/admin/passkey/login/begin', {
    method: 'POST',
    credentials: 'same-origin',
  });
  if (!beginRes.ok) {
    const data = await readJson(beginRes);
    serverFailure(data, 'The passkey sign-in could not start.', 'login');
  }
  const options = await beginRes.json();
  let credential;
  try {
    const getOptions = { publicKey: prepareRequestOptions(options) };
    if (conditional) {
      getOptions.mediation = 'conditional';
      getOptions.signal = signalController.signal;
    }
    credential = await navigator.credentials.get(getOptions);
  } catch (err) {
    if (conditional && (err?.name === 'AbortError' || err?.name === 'NotAllowedError')) return null;
    throw flowError(describePasskeyError(err, { flow: 'login' }), err?.name || 'Error', err);
  } finally {
    if (conditional && conditionalAbort === signalController) conditionalAbort = null;
  }
  if (!credential) {
    if (conditional) return null;
    throw flowError(
      describePasskeyError({ name: 'NotAllowedError' }, { flow: 'login' }),
      'NotAllowedError',
    );
  }
  const completeRes = await fetch('/api/admin/passkey/login/complete', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(assertionToJSON(credential)),
  });
  const data = await readJson(completeRes);
  if (!completeRes.ok || !data.success) {
    serverFailure(data, 'Passkey sign-in failed.', 'login');
  }
  return data;
}

/** Save a passkey for the signed-in email. The server locks the address. */
export async function registerPasskey({ email } = {}) {
  cancelConditionalSignIn();
  const support = await detectPasskeySupport();
  if (!support.webauthn) {
    throw flowError(describePasskeyError({ name: 'NotSupportedError' }, { flow: 'register' }), 'NotSupportedError');
  }
  const beginRes = await fetch('/api/admin/passkey/register/begin', {
    method: 'POST',
    credentials: 'same-origin',
    headers: adminCsrfHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(email ? { email } : {}),
  });
  if (!beginRes.ok) {
    const data = await readJson(beginRes);
    serverFailure(data, 'Sign in with the email link before adding a passkey.', 'register');
  }
  const options = await beginRes.json();
  let credential;
  try {
    credential = await navigator.credentials.create({ publicKey: prepareCreationOptions(options) });
  } catch (err) {
    throw flowError(describePasskeyError(err, { flow: 'register' }), err?.name || 'Error', err);
  }
  if (!credential) {
    throw flowError(
      describePasskeyError({ name: 'NotAllowedError' }, { flow: 'register' }),
      'NotAllowedError',
    );
  }
  const completeRes = await fetch('/api/admin/passkey/register/complete', {
    method: 'POST',
    credentials: 'same-origin',
    headers: adminCsrfHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(attestationToJSON(credential)),
  });
  const data = await readJson(completeRes);
  if (!completeRes.ok || !data.success) {
    serverFailure(data, 'The passkey could not be saved.', 'register');
  }
  return data;
}

export function passkeyNudgeSuppressed() {
  try {
    if (window.localStorage.getItem(NUDGE_NEVER_KEY) === '1') return true;
    if (window.sessionStorage.getItem(NUDGE_LATER_KEY) === '1') return true;
  } catch {
    return false;
  }
  return false;
}

export function dismissPasskeyNudge(choice) {
  try {
    if (choice === 'never') window.localStorage.setItem(NUDGE_NEVER_KEY, '1');
    else window.sessionStorage.setItem(NUDGE_LATER_KEY, '1');
  } catch {
    // Private browsing can reject storage. The prompt can return next time.
  }
}

export function shouldOfferPasskeyNudge({ method, platform, suppressed }) {
  const emailSignIn = method === 'email' || method === 'email_link' || method === 'email_code';
  return Boolean(emailSignIn && platform && !suppressed);
}
