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
});
