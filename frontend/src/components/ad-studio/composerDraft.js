/** In-progress Create Ad form. Text and choices only. No photos or file data. */

const PLATFORMS = new Set(['tiktok', 'instagram_reels', 'facebook']);
const THEMES = new Set([
  'home_tour',
  'myth_busting',
  'financing_tips',
  'clearance_alert',
  'behind_scenes',
  'customer_story',
  'comparison',
  'faq',
]);
const AVATARS = new Set(['tex_classic', 'tex_modern', 'tex_custom']);
const LANGUAGES = new Set(['en', 'es']);

function clip(value, max) {
  return String(value ?? '').replace(/\r\n/g, '\n').trim().slice(0, max);
}

function oneOf(value, allowed, fallback) {
  return allowed.has(value) ? value : fallback;
}

function cleanSpecNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function cleanSpecs(specs) {
  if (!specs || typeof specs !== 'object') return null;
  const beds = cleanSpecNumber(specs.beds);
  const baths = cleanSpecNumber(specs.baths);
  const sqFt = cleanSpecNumber(specs.sq_ft);
  if (beds == null && baths == null && sqFt == null) return null;
  const next = {};
  if (beds != null) next.beds = beds;
  if (baths != null) next.baths = baths;
  if (sqFt != null) next.sq_ft = sqFt;
  return next;
}

function cleanHome(home) {
  if (!home || typeof home !== 'object') return null;
  const modelName = clip(home.model_name, 200);
  if (!modelName) return null;
  const next = { model_name: modelName };
  const price = clip(home.display_price, 80);
  if (price) next.display_price = price;
  const specs = cleanSpecs(home.specs);
  if (specs) next.specs = specs;
  const id = clip(home.id, 80);
  if (id) next.id = id;
  return next;
}

function cleanTags(tags) {
  if (!Array.isArray(tags)) return [];
  return tags
    .map(tag => clip(tag, 80))
    .filter(Boolean)
    .slice(0, 30);
}

function cleanScriptBlock(block) {
  if (!block || typeof block !== 'object') return null;
  const hook = clip(block.hook, 500);
  const body = clip(block.body, 8000);
  const cta = clip(block.cta, 500);
  if (!hook && !body && !cta) return null;
  const next = { hook, body, cta };
  const duration = clip(block.duration_estimate, 40);
  const tone = clip(block.tone, 40);
  if (duration) next.duration_estimate = duration;
  if (tone) next.tone = tone;
  return next;
}

function cleanScript(script) {
  if (!script || typeof script !== 'object' || script.error) return null;
  const scripts = Array.isArray(script.scripts)
    ? script.scripts.map(cleanScriptBlock).filter(Boolean).slice(0, 3)
    : [];
  const single = cleanScriptBlock(script.script);
  if (!scripts.length && !single) return null;
  const next = { success: true, hashtags: cleanTags(script.hashtags) };
  if (scripts.length) next.scripts = scripts;
  if (single) next.script = single;
  const platform = oneOf(script.platform, PLATFORMS, '');
  if (platform) next.platform = platform;
  return next;
}

export function emptyComposerDraft() {
  return {
    platform: 'tiktok',
    theme: 'home_tour',
    homeName: '',
    homePrice: '',
    homeSpecs: null,
    customHook: '',
    language: 'en',
    avatar: 'tex_classic',
    customAvatarPrompt: '',
    variations: 1,
    activeVariation: 0,
    script: null,
    selectedHome: null,
  };
}

export function toComposerDraft(input) {
  const source = input && typeof input === 'object' ? input : {};
  const variations = [1, 2, 3].includes(Number(source.variations))
    ? Number(source.variations)
    : 1;
  let activeVariation = Number.isInteger(source.activeVariation) ? source.activeVariation : 0;
  if (activeVariation < 0 || activeVariation >= variations) activeVariation = 0;
  return {
    platform: oneOf(source.platform, PLATFORMS, 'tiktok'),
    theme: oneOf(source.theme, THEMES, 'home_tour'),
    homeName: clip(source.homeName, 200),
    homePrice: clip(source.homePrice, 80),
    homeSpecs: cleanSpecs(source.homeSpecs),
    customHook: clip(source.customHook, 500),
    language: oneOf(source.language, LANGUAGES, 'en'),
    avatar: oneOf(source.avatar, AVATARS, 'tex_classic'),
    customAvatarPrompt: clip(source.customAvatarPrompt, 500),
    variations,
    activeVariation,
    script: cleanScript(source.script),
    selectedHome: cleanHome(source.selectedHome),
  };
}

export function isComposerStarted(draft) {
  const empty = emptyComposerDraft();
  const current = toComposerDraft(draft);
  return current.platform !== empty.platform
    || current.theme !== empty.theme
    || current.homeName !== ''
    || current.homePrice !== ''
    || current.homeSpecs != null
    || current.customHook !== ''
    || current.language !== empty.language
    || current.avatar !== empty.avatar
    || current.customAvatarPrompt !== ''
    || current.variations !== 1
    || current.script != null
    || current.selectedHome != null;
}
