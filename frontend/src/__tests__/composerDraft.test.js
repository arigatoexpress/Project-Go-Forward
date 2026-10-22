import { describe, expect, it } from 'vitest';

import {
  emptyComposerDraft,
  isComposerStarted,
  toComposerDraft,
} from '../components/ad-studio/composerDraft';

describe('composer draft', () => {
  it('treats the default form as not started', () => {
    expect(isComposerStarted(emptyComposerDraft())).toBe(false);
    expect(isComposerStarted(null)).toBe(false);
  });

  it('keeps staff choices and drops photo fields', () => {
    const draft = toComposerDraft({
      platform: 'facebook',
      theme: 'not-a-theme',
      homeName: '  The Nassau  ',
      homePrice: 'Call for Price',
      customHook: 'Come see the porch',
      variations: 9,
      script: {
        platform: 'facebook',
        hashtags: ['#homes'],
        real_photos: ['https://example.test/a.jpg'],
        matterport_url: 'https://example.test/tour',
        scripts: [{
          hook: 'Look at this home',
          body: 'Three bedrooms.',
          cta: 'Come see it.',
        }],
      },
      selectedHome: {
        id: 'home-1',
        model_name: 'The Nassau',
        display_price: 'Call for Price',
        image_url: 'https://example.test/secret.jpg',
        real_photos: ['https://example.test/a.jpg'],
        specs: { beds: '3', baths: 2, sq_ft: 1450 },
      },
    });

    expect(draft.platform).toBe('facebook');
    expect(draft.theme).toBe('home_tour');
    expect(draft.homeName).toBe('The Nassau');
    expect(draft.homePrice).toBe('Call for Price');
    expect(draft.variations).toBe(1);
    expect(draft.script.real_photos).toBeUndefined();
    expect(draft.script.scripts[0].hook).toBe('Look at this home');
    expect(draft.selectedHome.image_url).toBeUndefined();
    expect(draft.selectedHome.real_photos).toBeUndefined();
    expect(draft.selectedHome.specs).toEqual({ beds: 3, baths: 2, sq_ft: 1450 });
    expect(JSON.stringify(draft)).not.toContain('example.test');
    expect(isComposerStarted(draft)).toBe(true);
  });
});
