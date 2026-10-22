import { afterEach, describe, expect, it } from 'vitest';

import {
  clearStaffDrafts,
  readComposerDraft,
  readStaffDrafts,
  writeComposerDraft,
  writeStaffDrafts,
} from '../components/ad-studio/staffDraftStorage';

function clearSession() {
  document.cookie = 'tho_csrf_token=; Max-Age=0; path=/';
  window.localStorage.clear();
}

describe('staff draft storage', () => {
  afterEach(() => {
    clearSession();
  });

  it('does not keep drafts when signed out', () => {
    writeStaffDrafts([{ script_text: 'Look at this home' }]);
    expect(readStaffDrafts()).toEqual([]);
    expect(window.localStorage.length).toBe(0);
  });

  it('keeps drafts for one staff session and hides them from another', () => {
    document.cookie = 'tho_csrf_token=session-a; path=/';
    window.localStorage.setItem('tho_passkey_email', 'Pat@TexasHomeOutlet.com');
    writeStaffDrafts([{ script_text: 'Pat draft' }]);
    expect(readStaffDrafts()).toEqual([{ script_text: 'Pat draft' }]);

    window.localStorage.setItem('tho_passkey_email', 'sam@texashomeoutlet.com');
    expect(readStaffDrafts()).toEqual([{ script_text: 'Pat draft' }]);

    document.cookie = 'tho_csrf_token=session-b; path=/';
    expect(readStaffDrafts()).toEqual([]);

    document.cookie = 'tho_csrf_token=session-a; path=/';
    expect(readStaffDrafts()).toEqual([{ script_text: 'Pat draft' }]);
  });

  it('drops the previous session when this session saves', () => {
    document.cookie = 'tho_csrf_token=session-a; path=/';
    writeStaffDrafts([{ script_text: 'A draft' }]);
    document.cookie = 'tho_csrf_token=session-b; path=/';
    writeStaffDrafts([{ script_text: 'B draft' }]);

    document.cookie = 'tho_csrf_token=session-a; path=/';
    expect(readStaffDrafts()).toEqual([]);
    document.cookie = 'tho_csrf_token=session-b; path=/';
    expect(readStaffDrafts()).toEqual([{ script_text: 'B draft' }]);
  });

  it('clears every saved draft', () => {
    document.cookie = 'tho_csrf_token=session-a; path=/';
    writeStaffDrafts([{ script_text: 'Look at this home' }]);
    document.cookie = 'tho_csrf_token=session-b; path=/';
    writeStaffDrafts([{ script_text: 'Another draft' }]);

    clearStaffDrafts();
    document.cookie = 'tho_csrf_token=session-a; path=/';
    expect(readStaffDrafts()).toEqual([]);
    document.cookie = 'tho_csrf_token=session-b; path=/';
    expect(readStaffDrafts()).toEqual([]);
  });

  it('keeps an in-progress ad for one sign-in and clears it on sign-out', () => {
    document.cookie = 'tho_csrf_token=session-a; path=/';
    writeComposerDraft({
      homeName: 'The Nassau',
      homePrice: '',
      selectedHome: {
        model_name: 'The Nassau',
        display_price: 'Call for Price',
        image_url: 'https://example.test/secret.jpg',
        real_photos: ['https://example.test/a.jpg'],
        specs: { beds: 3, baths: 2, sq_ft: 1200 },
      },
      script: {
        scripts: [{ hook: 'Look at this home', body: 'Three bedrooms.', cta: 'Come see it.' }],
        real_photos: ['https://example.test/a.jpg'],
      },
    });

    const saved = readComposerDraft();
    expect(saved.homeName).toBe('The Nassau');
    expect(saved.homePrice).toBe('');
    expect(saved.selectedHome).toEqual({
      model_name: 'The Nassau',
      display_price: 'Call for Price',
      specs: { beds: 3, baths: 2, sq_ft: 1200 },
    });
    expect(saved.script.scripts[0].hook).toBe('Look at this home');
    expect(JSON.stringify(saved)).not.toContain('example.test');

    document.cookie = 'tho_csrf_token=session-b; path=/';
    expect(readComposerDraft()).toBeNull();
    document.cookie = 'tho_csrf_token=session-a; path=/';
    expect(readComposerDraft().homeName).toBe('The Nassau');

    clearStaffDrafts();
    expect(readComposerDraft()).toBeNull();
    expect(readStaffDrafts()).toEqual([]);
  });
});
