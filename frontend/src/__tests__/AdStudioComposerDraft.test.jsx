import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import adminFetch from '../adminFetch';
import { clearStaffDrafts, readComposerDraft } from '../components/ad-studio/staffDraftStorage';
import AdStudio from '../pages/AdStudio';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

const OPENING_LINE = 'Write the first line yourself, or leave this blank.';

const scriptPayload = {
  success: true,
  script_id: 'SCR-1',
  platform: 'tiktok',
  hashtags: ['#manufacturedhomes'],
  real_photos: ['https://example.test/home.jpg'],
  scripts: [
    {
      hook: 'Look at this home',
      body: 'Three bedrooms on a quiet lot.',
      cta: 'Come see it at Texas Home Outlet.',
      duration_estimate: '20s',
    },
  ],
};

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(payload),
  };
}

function installFetch() {
  adminFetch.mockImplementation((url) => {
    if (url === '/api/marketing/voiceover-voices') {
      return Promise.resolve(jsonResponse({ success: true, voices: [] }));
    }
    if (url === '/api/marketing/gcp-readiness') {
      return Promise.resolve(jsonResponse({ success: true, ready: false }));
    }
    if (url === '/api/marketing/generate-script') {
      return Promise.resolve(jsonResponse(scriptPayload));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

function setStaffSession(token) {
  document.cookie = `tho_csrf_token=${token}; path=/`;
}

function clearStaffSession() {
  document.cookie = 'tho_csrf_token=; Max-Age=0; path=/';
  clearStaffDrafts();
}

async function openCreateAd() {
  render(<AdStudio onBack={vi.fn()} />);
  expect(await screen.findByText('Not available yet')).toBeInTheDocument();
}

describe('Ad Studio in-progress ad', () => {
  beforeEach(() => {
    adminFetch.mockReset();
    clearStaffSession();
  });

  afterEach(() => {
    cleanup();
    clearStaffSession();
    vi.restoreAllMocks();
  });

  it('cancels an in-progress ad and stays on the page', async () => {
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    installFetch();
    await openCreateAd();

    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Home name' }), {
      target: { value: 'The Nassau' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));
    expect(await screen.findByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);

    expect(globalThis.confirm).toHaveBeenCalledWith(expect.stringMatching(/Nothing is posted/));
    expect(screen.queryByRole('heading', { name: 'Ad Content Preview' })).not.toBeInTheDocument();
    expect(screen.queryByText('Look at this home')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Home name' })).toHaveValue('');
    expect(screen.getByRole('textbox', { name: 'Price' })).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Generate Script' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create Ad' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /publish/i })).not.toBeInTheDocument();
    expect(readComposerDraft()).toBeNull();
  });

  it('leaves the ad in place when cancel is declined', async () => {
    vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    installFetch();
    await openCreateAd();
    fireEvent.change(screen.getByRole('textbox', { name: 'Home name' }), {
      target: { value: 'The Nassau' },
    });

    fireEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);

    expect(screen.getByRole('textbox', { name: 'Home name' })).toHaveValue('The Nassau');
    expect(screen.getByRole('button', { name: 'Create Ad' })).toHaveAttribute('aria-current', 'page');
  });

  it('closes the preview without clearing the ad', async () => {
    installFetch();
    await openCreateAd();
    fireEvent.change(screen.getByRole('textbox', { name: 'Home name' }), {
      target: { value: 'The Nassau' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));
    expect(await screen.findByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));

    expect(screen.queryByRole('heading', { name: 'Ad Content Preview' })).not.toBeInTheDocument();
    expect(screen.getByText('Look at this home')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Home name' })).toHaveValue('The Nassau');
    expect(screen.getAllByRole('button', { name: 'Cancel' }).length).toBeGreaterThan(0);
  });

  it('restores an in-progress ad after reload for this sign-in only', async () => {
    setStaffSession('session-a');
    installFetch();
    await openCreateAd();
    fireEvent.change(screen.getByRole('textbox', { name: 'Home name' }), {
      target: { value: 'The Nassau' },
    });
    fireEvent.change(screen.getByPlaceholderText(OPENING_LINE), {
      target: { value: 'Come see the porch' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));
    expect((await screen.findAllByText('Look at this home')).length).toBeGreaterThan(0);
    expect(readComposerDraft()).toMatchObject({
      homeName: 'The Nassau',
      homePrice: '',
      customHook: 'Come see the porch',
    });
    expect(readComposerDraft().script.scripts[0].hook).toBe('Look at this home');
    expect(JSON.stringify(readComposerDraft())).not.toContain('example.test');
    expect(screen.getByRole('textbox', { name: 'Price' })).toHaveValue('');

    cleanup();
    installFetch();
    await openCreateAd();

    expect(screen.getByRole('textbox', { name: 'Home name' })).toHaveValue('The Nassau');
    expect(screen.getByPlaceholderText(OPENING_LINE)).toHaveValue('Come see the porch');
    expect(screen.getByRole('textbox', { name: 'Price' })).toHaveValue('');
    expect(screen.getByText('Look at this home')).toBeInTheDocument();
    expect(screen.getByText(/This ad was still here from your last visit on this browser/)).toBeInTheDocument();

    cleanup();
    setStaffSession('session-b');
    installFetch();
    await openCreateAd();

    expect(screen.getByRole('textbox', { name: 'Home name' })).toHaveValue('');
    expect(screen.getByPlaceholderText(OPENING_LINE)).toHaveValue('');
    expect(screen.queryByText('Look at this home')).not.toBeInTheDocument();
    expect(screen.queryByText(/This ad was still here/)).not.toBeInTheDocument();
  });

  it('does not keep an in-progress ad when nobody is signed in', async () => {
    installFetch();
    await openCreateAd();
    fireEvent.change(screen.getByRole('textbox', { name: 'Home name' }), {
      target: { value: 'The Nassau' },
    });
    expect(screen.getByText(/Sign in to keep it after a refresh/)).toBeInTheDocument();
    expect(readComposerDraft()).toBeNull();

    cleanup();
    installFetch();
    await openCreateAd();

    expect(screen.getByRole('textbox', { name: 'Home name' })).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });
});
