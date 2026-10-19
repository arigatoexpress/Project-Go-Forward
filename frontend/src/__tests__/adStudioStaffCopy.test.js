import { describe, expect, it } from 'vitest';

import {
  draftCardCopy,
  draftScriptPreview,
  draftScriptText,
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
    expect(plainQualityIssue('No real listing details. Add beds, baths, or square feet.'))
      .toMatch(/bedrooms, size, or Call for Price/i);
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
    expect(card.notice).toBe(
      'Saved on this browser until you sign out or your session times out. Nothing was posted.',
    );
    expect(card.notice).toMatch(/nothing was posted/i);
    expect(JSON.stringify(card)).not.toMatch(/DRAFT-TT|SCR-1|draft_ready/);
  });

  it('shows the script words and shortens a long script', () => {
    const post = {
      script_text: 'Look at this home\n\nThree bedrooms on a quiet lot.',
      caption: 'Come see it.',
    };
    expect(draftScriptText(post)).toBe(post.script_text);
    expect(draftScriptPreview(post)).toBe(post.script_text);

    const longScript = `${'Bedroom '.repeat(40)}ENDMARKER`;
    expect(longScript.length).toBeGreaterThan(280);
    const preview = draftScriptPreview({ script_text: longScript });
    expect(preview.endsWith('...')).toBe(true);
    expect(preview.length).toBeLessThan(longScript.length);
    expect(preview).not.toContain('ENDMARKER');
    expect(draftScriptText({ caption: 'Come see it.' })).toBe('Come see it.');
  });
});
