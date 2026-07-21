import { describe, it, expect, vi, beforeEach } from 'vitest';
import { captureUtmFromUrl, getUtmParams } from '../utils/utm';

describe('captureUtmFromUrl', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it('writes allowed fields to sessionStorage', () => {
    captureUtmFromUrl('?utm_source=google&utm_campaign=spring-sale&gclid=EAIaIQobChMI_test-123');
    const raw = sessionStorage.getItem('tho_utm');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw);
    expect(parsed.utm_source).toBe('google');
    expect(parsed.utm_campaign).toBe('spring-sale');
    expect(parsed.gclid).toBe('EAIaIQobChMI_test-123');
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

  it('captures allowlisted Google click IDs but rejects malformed values', () => {
    captureUtmFromUrl('?gbraid=GBRAID_abc-123&wbraid=%3Cscript%3E');
    const parsed = JSON.parse(sessionStorage.getItem('tho_utm'));
    expect(parsed.gbraid).toBe('GBRAID_abc-123');
    expect(parsed.wbraid).toBeUndefined();
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
