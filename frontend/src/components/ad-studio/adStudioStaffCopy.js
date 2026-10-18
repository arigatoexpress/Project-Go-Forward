/** Plain-language copy for the social Ad Studio. No provider calls. */

export const AI_MEDIA_UNAVAILABLE =
  'AI pictures and video are not available yet. You can still write a script and make a slideshow from the home’s photos. Ask the owner if you need an AI picture or video.';

export const VOICES_UNAVAILABLE =
  'Spoken voices are not available right now. You can still copy the script and record it yourself.';

export const VOICES_EMPTY =
  'No spoken voices are set up yet. You can still copy the script and record it yourself.';

export const IMAGE_CONFIRM =
  'This creates a picture with AI. It can use a paid AI tool. It does not post the ad. Continue?';

export const VOICE_CONFIRM =
  'This creates spoken audio with AI. It can use a paid AI tool. It does not post the ad. Continue?';

export const CLIP_CONFIRM =
  'This creates a short AI video. It can use a paid AI tool and may take a couple of minutes. It does not post the ad or turn on Google Ads. Continue?';

const PLATFORM_LABELS = {
  tiktok: 'TikTok',
  instagram: 'Instagram',
  instagram_reels: 'Instagram Reels',
  facebook: 'Facebook',
};

const THEME_LABELS = {
  home_tour: 'Home tour',
  myth_busting: 'Myth busting',
  financing_tips: 'Financing tips',
  clearance_alert: 'Clearance alert',
  behind_scenes: 'Behind the scenes',
  customer_story: 'Customer story',
  comparison: 'Comparison',
  faq: 'Common questions',
};

const QUALITY_LABELS = {
  hook_strength: 'Opening line',
  specificity: 'Real details',
  authenticity: 'Sounds like a person',
  cta_strength: 'Clear next step',
  structure: 'Easy to film',
};

const CONTENT_TYPE_LABELS = {
  video: 'Video',
  image: 'Picture',
  flyer: 'Flyer',
};

export function platformLabel(id) {
  const key = String(id || '').trim().toLowerCase();
  return PLATFORM_LABELS[key] || 'Social post';
}

export function themeLabel(id) {
  const key = String(id || '').trim().toLowerCase();
  return THEME_LABELS[key] || 'Content idea';
}

export function contentTypeLabel(id) {
  const key = String(id || '').trim().toLowerCase();
  return CONTENT_TYPE_LABELS[key] || 'Draft';
}

export function qualityScoreLabel(key) {
  return QUALITY_LABELS[key] || 'Script check';
}

export function plainQualityIssue(issue) {
  const text = String(issue || '');
  if (/hook too long/i.test(text)) return 'The opening line is too long. Aim for about 12 words.';
  if (/generic question/i.test(text)) return 'The opening line sounds generic. Lead with a specific claim.';
  if (/boring preamble/i.test(text)) return 'Start with the point. Skip “in this video.”';
  if (/no specific numbers|no real listing details/i.test(text)) return 'Add a real detail, such as bedrooms, size, or Call for Price.';
  if (/no rooms or features/i.test(text)) return 'Mention a real room or feature, such as the kitchen.';
  if (/banned words/i.test(text)) return 'Some words sound too salesy. Regenerate or rewrite those lines.';
  if (/exclamation marks/i.test(text)) return 'Use at most two exclamation marks.';
  if (/too many emojis/i.test(text)) return 'Use at most one emoji.';
  if (/run-on sentences/i.test(text)) return 'Break long sentences into short lines.';
  if (/clear action verb/i.test(text)) return 'The last line should tell people what to do, such as call or book a tour.';
  if (/cta too short/i.test(text)) return 'The last line needs a clear next step.';
  if (/\[SHOT\]/i.test(text)) return 'Add a short camera note for each scene, or regenerate the script.';
  if (/timing markers/i.test(text)) return 'Add rough timing, such as the first three seconds, so the video has a pace.';
  if (/too long for tiktok/i.test(text)) return 'This is long for TikTok. Shorten it.';
  if (/too short for facebook/i.test(text)) return 'Facebook posts can use a little more detail.';
  return 'This draft needs another pass. Regenerate it or edit the wording.';
}

export function plainRecommendation(text) {
  const value = String(text || '');
  if (/tiktok or meta analytics/i.test(value)) {
    return 'Live view and follower counts are not connected yet. Do not treat the numbers here as real ad results.';
  }
  if (/inventory-backed idea lane/i.test(value)) {
    return 'Start from Content Ideas. They use the homes on the lot right now.';
  }
  if (/homes with usable photos/i.test(value)) return value;
  if (/pre-owned homes/i.test(value)) return value;
  if (!value.trim()) return '';
  return 'Ask the owner before you treat this note as a live result.';
}

const READINESS_LABELS = {
  video_assets_ready: 'Slideshow videos made here',
  image_creatives_ready: 'Pictures made here',
  inventory_backed_ideas: 'Homes ready to feature',
};

export function readinessItemLabel(type) {
  return READINESS_LABELS[type] || 'Item on this screen';
}

export function readinessMetric(item) {
  if (item?.engagement_label === 'pending publish data' || item?.engagement_rate === 'local') {
    return 'Not posted yet';
  }
  return item?.engagement_label || 'On this screen';
}

export function draftCardCopy(post) {
  const platform = platformLabel(post?.platform);
  const ready = post?.status === 'draft_ready';
  return {
    title: `${platform} draft`,
    kind: contentTypeLabel(post?.content_type),
    status: ready ? 'Ready to review here' : 'Saved on this screen',
    notice: post?.persisted === false
      ? 'This stays on this screen only. Refreshing or leaving removes it. Nothing was posted.'
      : 'Nothing was posted to TikTok, Instagram, or Facebook.',
  };
}
