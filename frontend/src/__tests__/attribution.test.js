import { beforeEach, describe, expect, it } from 'vitest';
import { getJourneyAttribution, getJourneyId } from '../utils/attribution';

const LANDING = '/inventory?utm_source=google&utm_medium=cpc'
  + '&utm_campaign=tho_search_high_intent_huffman&utm_content=rsa'
  + '&utm_term=manufactured%20homes&gclid=test-gclid-123456'
  + '&wbraid=WBRAID_valid1&gbraid=GBRAID_valid1&email=buyer@example.com';

describe('anonymous journey attribution', () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.history.replaceState({}, '', '/');
  });

  it('keeps one opaque id for the current tab session', () => {
    const first = getJourneyId();
    expect(first).toMatch(/^j_[0-9a-f]{32}$/);
    expect(getJourneyId()).toBe(first);
    expect(getJourneyAttribution()).toEqual({ journey_id: first });
  });

  it('replaces a malformed stored value', () => {
    sessionStorage.setItem('tho_journey_id', 'buyer@example.com');
    expect(getJourneyId()).toMatch(/^j_[0-9a-f]{32}$/);
    expect(getJourneyId()).not.toContain('@');
  });

  it('keeps the landing campaign with the journey after the query string is gone', () => {
    window.history.replaceState({}, '', LANDING);
    const landing = getJourneyAttribution();
    window.history.pushState({}, '', '/appointments');

    expect(window.location.search).toBe('');
    expect(getJourneyAttribution()).toEqual(landing);
    expect(landing).toMatchObject({
      utm_source: 'google',
      utm_medium: 'cpc',
      utm_campaign: 'tho_search_high_intent_huffman',
      utm_content: 'rsa',
      utm_term: 'manufactured homes',
      gclid: 'test-gclid-123456',
      wbraid: 'WBRAID_valid1',
      gbraid: 'GBRAID_valid1',
    });
    expect(landing.journey_id).toMatch(/^j_[0-9a-f]{32}$/);
    expect(landing).not.toHaveProperty('email');
    expect(JSON.stringify(landing)).not.toContain('buyer@example.com');
  });

  it('drops a malformed click id and still keeps the journey', () => {
    window.history.replaceState({}, '', '/inventory?gclid=%3Cscript%3E&utm_source=google');
    const attribution = getJourneyAttribution();
    expect(attribution.utm_source).toBe('google');
    expect(attribution).not.toHaveProperty('gclid');
    expect(attribution.journey_id).toMatch(/^j_[0-9a-f]{32}$/);
  });
});
