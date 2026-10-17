import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import adminFetch from '../adminFetch';
import AdStudio from '../pages/AdStudio';

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
          top_performing_content: [{
            type: 'inventory_backed_ideas',
            views: 17,
            views_label: '17 homes available',
            engagement_rate: 'ready',
            engagement_label: '8 with photos',
          }],
          recommendations: [
            'Connect TikTok or Meta analytics before treating reach, follower, or DM metrics as live.',
            'Use the inventory-backed idea lane for 17 current homes.',
          ],
        }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
  });

  it('shows local readiness and does not render unavailable platform KPIs as zero', async () => {
    render(<AdStudio onBack={vi.fn()} />);

    expect(await screen.findByText('Not available yet')).toBeInTheDocument();
    expect(screen.getByText('AI pictures & video')).toBeInTheDocument();
    expect(screen.queryByText('GCP AI')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Analytics' }));

    expect(await screen.findByText('Live social numbers are not connected')).toBeInTheDocument();
    expect(screen.getByText(/Do not treat the numbers here as real ad results/i)).toBeInTheDocument();
    expect(screen.getByText(/Start from Content Ideas/i)).toBeInTheDocument();
    expect(screen.getByText('Homes ready to feature')).toBeInTheDocument();
    expect(screen.queryByText(/inventory-backed idea lane/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Meta analytics/i)).not.toBeInTheDocument();
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

  it('opens Google Ads when the staff entry is /ad-studio', async () => {
    render(<AdStudio onBack={vi.fn()} initialTab="paid-search" />);

    expect(screen.getByRole('button', { name: 'Google Ads' })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByRole('alert')).toHaveTextContent(/Google Ads is unavailable right now/i);
    expect(screen.queryByRole('button', { name: /turn ads on|publish/i })).not.toBeInTheDocument();
  });
});
