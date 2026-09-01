import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import adminFetch from '../adminFetch';
import AdStudio, { readJsonOrThrow } from '../pages/AdStudio';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

function okResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(payload),
  };
}

describe('Ad Studio truth contracts', () => {
  beforeEach(() => {
    adminFetch.mockReset();
    adminFetch.mockImplementation((url) => {
      if (url === '/api/marketing/voiceover-voices') {
        return Promise.resolve(okResponse({ success: true, voices: [] }));
      }
      if (url === '/api/marketing/gcp-readiness') {
        return Promise.resolve(okResponse({ success: true, ready: false }));
      }
      if (url === '/api/marketing/analytics') {
        return Promise.resolve(okResponse({
          success: true,
          source: 'local_readiness',
          social_analytics_connected: false,
          disclaimer: 'Live social-platform analytics are not connected.',
          summary: {
            total_views: 0,
            total_engagement: 0,
            new_followers: 0,
            dms_received: 0,
            leads_generated: 0,
            generated_images: 2,
            generated_videos: 1,
            inventory_count: 17,
            photo_ready_homes: 8,
          },
          top_performing_content: [],
          recommendations: [],
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
  });

  it('shows local readiness and does not render unavailable platform KPIs as zero', async () => {
    render(<AdStudio onBack={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Analytics' }));

    expect(await screen.findByText('Platform analytics unavailable')).toBeInTheDocument();
    expect(screen.getByText('Generated Images')).toBeInTheDocument();
    expect(screen.getByText('Generated Videos')).toBeInTheDocument();
    expect(screen.getByText('Inventory Homes')).toBeInTheDocument();
    expect(screen.getByText('Photo-ready Homes')).toBeInTheDocument();
    expect(screen.queryByText('Total Views')).not.toBeInTheDocument();
    expect(screen.queryByText('Engagements')).not.toBeInTheDocument();
    expect(screen.queryByText('New Followers')).not.toBeInTheDocument();
    expect(screen.queryByText('DMs Received')).not.toBeInTheDocument();
    expect(screen.queryByText('Leads Generated')).not.toBeInTheDocument();
  });

  it('treats a success-false HTTP 200 envelope as a failure', async () => {
    await expect(readJsonOrThrow(
      okResponse({ success: false, error: 'Draft preparation was rejected.' }),
      'Draft preparation failed',
    )).rejects.toThrow('Draft preparation was rejected.');
  });

  it('does not navigate to Drafts when draft preparation fails', async () => {
    adminFetch.mockImplementation((url) => {
      if (url === '/api/marketing/voiceover-voices') {
        return Promise.resolve(okResponse({ success: true, voices: [] }));
      }
      if (url === '/api/marketing/gcp-readiness') {
        return Promise.resolve(okResponse({ success: true, ready: false }));
      }
      if (url === '/api/marketing/generate-script') {
        return Promise.resolve(okResponse({
          success: true,
          platform: 'tiktok',
          script_id: 'SCRIPT-1',
          hashtags: ['#TexasHomes'],
          script: {
            hook: 'Tour this home',
            body: 'A reviewed home tour.',
            cta: 'Call today.',
            suggested_image_prompts: [],
          },
        }));
      }
      if (url === '/api/marketing/schedule') {
        return Promise.resolve(okResponse({
          success: false,
          error: 'The draft could not be prepared. Please try again.',
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<AdStudio onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /^Generate Script$/ }));

    expect(await screen.findByText('Ad Content Preview')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Prepare Draft' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The draft could not be prepared. Please try again.',
    );
    expect(screen.getByText('Ad Content Preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drafts' })).not.toHaveAttribute('aria-current');
  });
});
