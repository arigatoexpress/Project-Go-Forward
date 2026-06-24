import { describe, it, expect, vi, beforeEach } from 'vitest';
import { captureUtmFromUrl, getUtmParams } from '../utils/utm';

describe('captureUtmFromUrl', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it('writes allowed fields to sessionStorage', () => {
    captureUtmFromUrl('?utm_source=instagram&utm_campaign=spring-sale');
    const raw = sessionStorage.getItem('tho_utm');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw);
    expect(parsed.utm_source).toBe('instagram');
    expect(parsed.utm_campaign).toBe('spring-sale');
  });

  it('first capture wins', () => {
    captureUtmFromUrl('?utm_source=instagram&utm_campaign=spring-sale');
    captureUtmFromUrl('?utm_source=facebook&utm_campaign=summer-sale');
    const parsed = JSON.parse(sessionStorage.getItem('tho_utm'));
    expect(parsed.utm_source).toBe('instagram');
    expect(parsed.utm_campaign).toBe('spring-sale');
  });

  it('ignores arbitrary query params', () => {
    captureUtmFromUrl('?utm_source=ig&foo=bar');
    const parsed = JSON.parse(sessionStorage.getItem('tho_utm'));
    expect(parsed.utm_source).toBe('ig');
    expect(parsed.foo).toBeUndefined();
  });

  it('captures referrer when present', () => {
    vi.stubGlobal('document', { referrer: 'https://google.com' });
    captureUtmFromUrl('?utm_source=google');
    const parsed = JSON.parse(sessionStorage.getItem('tho_utm'));
    expect(parsed.referrer).toBe('https://google.com');
  });
});

describe('getUtmParams', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it('returns parsed object after capture', () => {
    captureUtmFromUrl('?utm_source=ig&utm_medium=cpc');
    expect(getUtmParams()).toEqual({
      utm_source: 'ig',
      utm_medium: 'cpc',
    });
  });

  it('returns empty object when none stored', () => {
    expect(getUtmParams()).toEqual({});
  });

  it('returns empty object on malformed JSON', () => {
    sessionStorage.setItem('tho_utm', 'not-json');
    expect(getUtmParams()).toEqual({});
  });
});
