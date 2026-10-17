import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import adminFetch from '../adminFetch';
import { AI_MEDIA_UNAVAILABLE, VOICES_UNAVAILABLE } from '../components/ad-studio/adStudioStaffCopy';
import AdStudio from '../pages/AdStudio';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

const scriptPayload = {
  success: true,
  script_id: 'SCR-SECRET',
  platform: 'tiktok',
  model_used: 'configured-model',
  hashtags: ['#manufacturedhomes'],
  quality: {
    average: 8,
    passed: true,
    scores: { hook_strength: 8, cta_strength: 7 },
    issues: ['No [SHOT] markers — add visual direction for each scene'],
  },
  scripts: [{
    hook: 'Look at this home',
    body: '[SHOT: kitchen] Three bedrooms.',
    cta: 'Come see it.',
    duration_estimate: '20s',
  }],
};

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: vi.fn().mockResolvedValue(payload),
  };
}

describe('Ad Studio staff language', () => {
  beforeEach(() => {
    adminFetch.mockReset();
    vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
  });

  it('hides setup jargon and asks before a paid AI picture', async () => {
    adminFetch.mockImplementation((url) => {
      if (url === '/api/marketing/voiceover-voices') {
        return Promise.reject(new Error('TypeError: Failed to fetch'));
      }
      if (url === '/api/marketing/gcp-readiness') {
        return Promise.resolve(jsonResponse({
          success: true,
          ready: false,
          requirements: ['Install/repair google-genai: Error: /secret/sdk'],
        }));
      }
      if (url === '/api/marketing/generate-script') {
        return Promise.resolve(jsonResponse(scriptPayload));
      }
      if (url === '/api/marketing/generate-image') {
        return Promise.resolve(jsonResponse({ success: true }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Generate Script' }));

    expect(await screen.findByText(AI_MEDIA_UNAVAILABLE)).toBeInTheDocument();
    expect(screen.getByText(/Words in \[brackets\] are camera notes/i)).toBeInTheDocument();
    expect(screen.getAllByText('Opening line').length).toBeGreaterThan(0);
    expect(screen.getByText('Clear next step')).toBeInTheDocument();
    expect(screen.getAllByText(/camera note/i).length).toBeGreaterThan(0);
    expect(screen.queryByText('SCR-SECRET')).not.toBeInTheDocument();
    expect(screen.queryByText('configured-model')).not.toBeInTheDocument();
    expect(screen.queryByText(/google-genai|\/secret\/sdk|GCP AI|GenAI|Veo/i)).not.toBeInTheDocument();
    expect(screen.getByText(VOICES_UNAVAILABLE)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Describe the image you want to generate...'), {
      target: { value: 'A bright kitchen' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Image' }));

    expect(globalThis.confirm).toHaveBeenCalledWith(expect.stringMatching(/does not post the ad/i));
    expect(adminFetch.mock.calls.map(([url]) => url)).not.toContain('/api/marketing/generate-image');
  });

  it('shows an empty idea list instead of a blank page', async () => {
    adminFetch.mockImplementation((url) => {
      if (url === '/api/marketing/voiceover-voices') {
        return Promise.resolve(jsonResponse({ success: true, voices: [] }));
      }
      if (url === '/api/marketing/gcp-readiness') {
        return Promise.resolve(jsonResponse({ success: true, ready: true }));
      }
      if (url === '/api/marketing/trending-ideas') {
        return Promise.resolve(jsonResponse({
          success: true,
          inventory_count: 0,
          content_ideas: [],
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Content Ideas' }));

    expect(await screen.findByText('No ideas came back.')).toBeInTheDocument();
    expect(screen.getByText(/Nothing is posted from this page/i)).toBeInTheDocument();
  });
});
