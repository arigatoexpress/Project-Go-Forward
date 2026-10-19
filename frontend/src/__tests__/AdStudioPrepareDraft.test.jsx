import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import adminFetch from '../adminFetch';
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

describe('Ad Studio prepare draft', () => {
  beforeEach(() => {
    adminFetch.mockReset();
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
    expect(screen.getByText(/stays on this screen only/i)).toBeInTheDocument();
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
});
