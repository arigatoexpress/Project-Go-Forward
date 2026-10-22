import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  PASSKEY_EMAIL_FALLBACK,
  PASSKEY_OLD_SITE,
  PASSKEY_UNSUPPORTED_DEVICE,
  describePasskeyError,
  detectPasskeySupport,
  dismissPasskeyNudge,
  passkeyNudgeSuppressed,
  shouldOfferPasskeyNudge,
} from './passkey';

describe('passkey error copy', () => {
  it.each([
    ['NotAllowedError', 'closed by the user', `${PASSKEY_EMAIL_FALLBACK} The passkey prompt was closed before it finished.`],
    ['AbortError', 'aborted', `${PASSKEY_EMAIL_FALLBACK} The passkey prompt was closed before it finished.`],
    ['TimeoutError', 'slow', `${PASSKEY_EMAIL_FALLBACK} The passkey prompt timed out.`],
    ['NotAllowedError', 'The operation timed out', `${PASSKEY_EMAIL_FALLBACK} The passkey prompt timed out.`],
    ['InvalidStateError', 'already', `${PASSKEY_EMAIL_FALLBACK} This passkey is already saved for Texas Home Outlet. Try signing in with it.`],
    ['SecurityError', 'wrong origin', PASSKEY_OLD_SITE],
    ['ServerError', 'Authentication verification failed.', PASSKEY_OLD_SITE],
    ['ServerError', 'Unknown credential', PASSKEY_OLD_SITE],
    ['NotSupportedError', 'nope', `${PASSKEY_EMAIL_FALLBACK} This browser can’t use a passkey.`],
    ['ServerError', 'Sign-in is temporarily unavailable.', 'Sign-in is temporarily unavailable.'],
  ])('maps %s (%s)', (name, message, expected) => {
    const flow = name === 'InvalidStateError' ? 'register' : 'login';
    expect(describePasskeyError({ name, message }, { flow })).toBe(expected);
  });

  it('uses the already-saved sentence for sign-in and for registration', () => {
    const err = { name: 'InvalidStateError', message: 'duplicate' };
    const expected = `${PASSKEY_EMAIL_FALLBACK} This passkey is already saved for Texas Home Outlet. Try signing in with it.`;
    expect(describePasskeyError(err, { flow: 'login' })).toBe(expected);
    expect(describePasskeyError(err, { flow: 'register' })).toBe(expected);
  });

  it('leads a registration failure with the email link and never the PIN', () => {
    const message = describePasskeyError(
      { name: 'Error', message: 'The passkey could not be saved.' },
      { flow: 'register' },
    );
    expect(message.startsWith(PASSKEY_EMAIL_FALLBACK)).toBe(true);
    expect(message.toLowerCase()).not.toContain('pin');
  });

  it('uses the unsupported-device sentence only as that constant', () => {
    expect(PASSKEY_UNSUPPORTED_DEVICE).toBe('This device can’t save a passkey. Use the email link.');
  });
});

describe('passkey device support', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reports an unsupported browser', async () => {
    vi.stubGlobal('PublicKeyCredential', undefined);
    delete window.PublicKeyCredential;
    await expect(detectPasskeySupport()).resolves.toEqual({
      webauthn: false,
      platform: false,
      conditional: false,
    });
  });

  it('hides save when WebAuthn exists but no platform authenticator does', async () => {
    vi.stubGlobal('PublicKeyCredential', {
      isUserVerifyingPlatformAuthenticatorAvailable: vi.fn(async () => false),
      isConditionalMediationAvailable: vi.fn(async () => true),
    });
    await expect(detectPasskeySupport()).resolves.toEqual({
      webauthn: true,
      platform: false,
      conditional: true,
    });
  });

  it('offers save and conditional UI when the platform authenticator is available', async () => {
    vi.stubGlobal('PublicKeyCredential', {
      isUserVerifyingPlatformAuthenticatorAvailable: vi.fn(async () => true),
      isConditionalMediationAvailable: vi.fn(async () => true),
    });
    await expect(detectPasskeySupport()).resolves.toEqual({
      webauthn: true,
      platform: true,
      conditional: true,
    });
  });
});

describe('passkey nudge', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('asks once after email sign-in on a device that can save a passkey', () => {
    expect(shouldOfferPasskeyNudge({ method: 'email', platform: true, suppressed: false })).toBe(true);
    expect(shouldOfferPasskeyNudge({ method: 'pin', platform: true, suppressed: false })).toBe(false);
    expect(shouldOfferPasskeyNudge({ method: 'passkey', platform: true, suppressed: false })).toBe(false);
    expect(shouldOfferPasskeyNudge({ method: 'email', platform: false, suppressed: false })).toBe(false);
  });

  it('remembers Not now for this visit and Don’t ask again after that', () => {
    dismissPasskeyNudge('later');
    expect(passkeyNudgeSuppressed()).toBe(true);
    window.sessionStorage.clear();
    expect(passkeyNudgeSuppressed()).toBe(false);
    dismissPasskeyNudge('never');
    expect(passkeyNudgeSuppressed()).toBe(true);
    window.sessionStorage.clear();
    expect(passkeyNudgeSuppressed()).toBe(true);
  });
});
