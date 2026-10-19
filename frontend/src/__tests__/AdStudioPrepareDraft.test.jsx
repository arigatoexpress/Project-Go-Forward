import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import adminFetch from '../adminFetch';
import { clearStaffDrafts, readStaffDrafts, writeStaffDrafts } from '../components/ad-studio/staffDraftStorage';
import AdStudio from '../pages/AdStudio';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

const DRAFT_NOT_SAVED = "The draft wasn't saved. Please try again.";

const scriptPayload = {
  success: true,
  script_id: 'SCR-1',
  platform: 'tiktok',
  hashtags: ['#manufacturedhomes'],
  scripts: [
    {
      hook: 'Look at this home',
      body: 'Three bedrooms on a quiet lot.',
      cta: 'Come see it at Texas Home Outlet.',
      duration_estimate: '20s',
    },
  ],
};

const preparedDraft = {
  success: true,
  post_id: 'DRAFT-TT-ABC123',
  platform: 'tiktok',
  content_type: 'video',
  status: 'draft_ready',
  persisted: false,
  retention: 'response_only',
  script_reference: 'SCR-1',
  publish_blocked_reason: 'Draft prepared locally; no social platform request was made.',
};

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: vi.fn().mockResolvedValue(payload),
  };
}

function installFetch({ schedule } = {}) {
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
    if (url === '/api/marketing/schedule') {
      return Promise.resolve(schedule);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

async function generateAndOpenPreview() {
  render(<AdStudio onBack={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));
  expect(await screen.findByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();
}

function setStaffSession(token) {
  document.cookie = `tho_csrf_token=${token}; path=/`;
}

function clearStaffSession() {
  document.cookie = 'tho_csrf_token=; Max-Age=0; path=/';
  window.localStorage.removeItem('tho_passkey_email');
  clearStaffDrafts();
}

function storedDraftKeys() {
  const keys = [];
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i);
    if (key && key.startsWith('tho-ad-studio-drafts:')) keys.push(key);
  }
  return keys;
}

describe('Ad Studio prepare draft', () => {
  beforeEach(() => {
    adminFetch.mockReset();
    clearStaffSession();
  });

  afterEach(() => {
    cleanup();
    clearStaffSession();
  });

  it('keeps the preview open when preparation returns an error field on HTTP 200', async () => {
    installFetch({
      schedule: jsonResponse({ error: 'Failed to prepare draft. Please try again.' }),
    });
    await generateAndOpenPreview();

    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(DRAFT_NOT_SAVED);
    expect(screen.getByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Prepare Draft' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Drafts' })).not.toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText('TikTok draft')).not.toBeInTheDocument();
    expect(screen.queryByText(/Response ID:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /publish/i })).not.toBeInTheDocument();
  });

  it('keeps the preview open when preparation returns a failed HTTP status', async () => {
    installFetch({
      schedule: jsonResponse(
        { success: false, status_code: 500, message: DRAFT_NOT_SAVED },
        { ok: false, status: 500 },
      ),
    });
    await generateAndOpenPreview();

    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(DRAFT_NOT_SAVED);
    expect(screen.getByRole('heading', { name: 'Ad Content Preview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drafts' })).not.toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText(/Response ID:/)).not.toBeInTheDocument();
  });

  it('opens Drafts and closes the preview when preparation succeeds', async () => {
    installFetch({ schedule: jsonResponse(preparedDraft) });
    await generateAndOpenPreview();

    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));

    expect(await screen.findByText('TikTok draft')).toBeInTheDocument();
    expect(screen.getByText('Ready to review here')).toBeInTheDocument();
    expect(screen.getByText('Saved on this browser until you sign out. Nothing was posted.')).toBeInTheDocument();
    expect(screen.getByText(/Look at this home/)).toBeInTheDocument();
    expect(screen.getByText(/Three bedrooms on a quiet lot/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy' })).toBeEnabled();
    expect(screen.queryByText(/Response ID:/)).not.toBeInTheDocument();
    expect(screen.queryByText('draft_ready')).not.toBeInTheDocument();
    expect(screen.queryByText(/SCR-1/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drafts' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('heading', { name: 'Ad Content Preview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /publish/i })).not.toBeInTheDocument();
  });

  it('still shows a script error returned on HTTP 200 without opening the draft preview', async () => {
    adminFetch.mockImplementation((url) => {
      if (url === '/api/marketing/voiceover-voices') {
        return Promise.resolve(jsonResponse({ success: true, voices: [] }));
      }
      if (url === '/api/marketing/gcp-readiness') {
        return Promise.resolve(jsonResponse({ success: true, ready: false }));
      }
      if (url === '/api/marketing/generate-script') {
        return Promise.resolve(jsonResponse({
          error: 'The script was not written. Nothing was posted. Please try again.',
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));

    expect(await screen.findByText('The script was not written. Nothing was posted. Please try again.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ad Content Preview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Prepare Draft' })).not.toBeInTheDocument();
  });

  it('copies the full script and says Copied', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    installFetch({ schedule: jsonResponse(preparedDraft) });
    await generateAndOpenPreview();

    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));
    expect(await screen.findByRole('button', { name: 'Copy' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Copied');
    expect(writeText).toHaveBeenCalledWith(
      'Look at this home\n\nThree bedrooms on a quiet lot.\n\nCome see it at Texas Home Outlet.',
    );
  });

  it('shortens a long script on the card and still copies every word', async () => {
    const scriptText = `${'Look at this home. '.repeat(30)}ENDMARKER`;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    setStaffSession('session-a');
    writeStaffDrafts([{
      post_id: 'DRAFT-LONG',
      platform: 'tiktok',
      content_type: 'video',
      status: 'draft_ready',
      persisted: false,
      script_text: scriptText,
    }]);

    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Drafts' }));

    const shown = await screen.findByText(/Look at this home\./);
    expect(shown.textContent.endsWith('...')).toBe(true);
    expect(shown.textContent).not.toContain('ENDMARKER');
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Copied');
    expect(writeText).toHaveBeenCalledWith(scriptText);
  });

  it('keeps the draft after a refresh for this staff session only', async () => {
    setStaffSession('session-a');
    window.localStorage.setItem('tho_passkey_email', 'pat@texashomeoutlet.com');
    installFetch({ schedule: jsonResponse(preparedDraft) });
    await generateAndOpenPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));
    expect(await screen.findByText(/Look at this home/)).toBeInTheDocument();
    expect(readStaffDrafts()).toHaveLength(1);

    cleanup();
    installFetch({ schedule: jsonResponse(preparedDraft) });
    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Drafts' }));
    expect(await screen.findByText(/Three bedrooms on a quiet lot/)).toBeInTheDocument();

    cleanup();
    setStaffSession('session-b');
    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Drafts' }));
    expect(await screen.findByText('No drafts prepared yet')).toBeInTheDocument();
    expect(screen.queryByText(/Look at this home/)).not.toBeInTheDocument();
  });

  it('does not keep a draft when there is no staff session', async () => {
    installFetch({ schedule: jsonResponse(preparedDraft) });
    await generateAndOpenPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));
    expect(await screen.findByText('TikTok draft')).toBeInTheDocument();
    expect(readStaffDrafts()).toEqual([]);
    expect(storedDraftKeys()).toEqual([]);

    cleanup();
    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Drafts' }));
    expect(await screen.findByText('No drafts prepared yet')).toBeInTheDocument();
  });
});
