import { beforeEach, describe, expect, it } from 'vitest';
import { getJourneyAttribution, getJourneyId } from '../utils/attribution';

describe('anonymous journey attribution', () => {
  beforeEach(() => sessionStorage.clear());

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
});
