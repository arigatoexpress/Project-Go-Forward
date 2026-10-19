import { afterEach, describe, expect, it } from 'vitest';

import {
  clearStaffDrafts,
  readStaffDrafts,
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
});
