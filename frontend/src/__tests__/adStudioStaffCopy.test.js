import { describe, expect, it } from 'vitest';

import {
  draftCardCopy,
  plainQualityIssue,
  plainRecommendation,
  platformLabel,
  qualityScoreLabel,
  readinessItemLabel,
  readinessMetric,
  themeLabel,
} from '../components/ad-studio/adStudioStaffCopy';

describe('Ad Studio staff copy', () => {
  it('names platforms and themes in everyday words', () => {
    expect(platformLabel('instagram_reels')).toBe('Instagram Reels');
    expect(platformLabel('tiktok')).toBe('TikTok');
    expect(platformLabel('mystery')).toBe('Social post');
    expect(themeLabel('faq')).toBe('Common questions');
    expect(themeLabel('home_tour')).toBe('Home tour');
  });

  it('hides script-score field names and camera-marker jargon', () => {
    expect(qualityScoreLabel('hook_strength')).toBe('Opening line');
    expect(qualityScoreLabel('cta_strength')).toBe('Clear next step');
    expect(qualityScoreLabel('unknown_score')).toBe('Script check');
    expect(plainQualityIssue('No [SHOT] markers — add visual direction for each scene'))
      .toMatch(/camera note/i);
    expect(plainQualityIssue('CTA needs a clear action verb (call, DM, link in bio)'))
      .toMatch(/book a tour/i);
    expect(plainQualityIssue('Traceback (most recent call last)')).toMatch(/another pass/i);
    expect(plainQualityIssue('Hook too long — should be under 12 words')).not.toMatch(/hook/i);
  });

  it('rewrites analytics codes and does not echo an internal draft id', () => {
    expect(readinessItemLabel('video_assets_ready')).toBe('Slideshow videos made here');
    expect(readinessItemLabel('inventory_backed_ideas')).toBe('Homes ready to feature');
    expect(readinessMetric({ engagement_label: 'pending publish data' })).toBe('Not posted yet');
    expect(plainRecommendation(
      'Connect TikTok or Meta analytics before treating reach, follower, or DM metrics as live.',
    )).toMatch(/not connected yet/i);
    expect(plainRecommendation('Use the inventory-backed idea lane for 17 current homes.'))
      .toMatch(/Content Ideas/);
    expect(plainRecommendation('Prioritize the 8 homes with usable photos for fastest video creation.'))
      .toMatch(/usable photos/);

    const card = draftCardCopy({
      platform: 'tiktok',
      content_type: 'video',
      status: 'draft_ready',
      persisted: false,
      post_id: 'DRAFT-TT-ABC123',
      script_reference: 'SCR-1',
      publish_blocked_reason: 'Draft prepared locally; no social platform request was made.',
    });
    expect(card.title).toBe('TikTok draft');
    expect(card.status).toBe('Ready to review here');
    expect(card.notice).toMatch(/nothing was posted/i);
    expect(JSON.stringify(card)).not.toMatch(/DRAFT-TT|SCR-1|draft_ready/);
  });
});
