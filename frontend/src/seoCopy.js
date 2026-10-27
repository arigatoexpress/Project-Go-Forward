import {
  BUSINESS_CITY,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_NAME,
  BUSINESS_PHONE,
  BUSINESS_STATE,
} from './constants';
import legalPages from './content/legalPages.json';

// Neutral lines for facts the owner has not confirmed. Keep these exact.
export const FINANCING_LINE =
  'Ask our team about financing options. A lender reviews credit, income, and the land situation, and the lender decides whether a loan is approved.';
export const DELIVERY_LINE = 'Delivery available, ask for details.';
export const WARRANTY_LINE = 'Warranty coverage varies by manufacturer, ask a manager.';

// Public titles and descriptions. Keep these in step with seo_routes.py.
// Titles are at most 60 characters. Descriptions are at most 155.
// Copy stays on the phone, address, and hours already shown on the site.

export const HOME_TITLE = `Manufactured Homes for Sale in ${BUSINESS_CITY} | ${BUSINESS_NAME}`;
export const HOME_H1 = `Manufactured and Mobile Homes for Sale in ${BUSINESS_CITY}, ${BUSINESS_STATE}`;
export const HOME_DESCRIPTION =
  `Browse manufactured and mobile homes for sale at ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}. `
  + `Call or text ${BUSINESS_PHONE} to confirm price, financing, availability.`;

export const INVENTORY_TITLE = `Home Inventory in ${BUSINESS_CITY}, ${BUSINESS_STATE} | ${BUSINESS_NAME}`;
export const INVENTORY_H1 = `Home Inventory in ${BUSINESS_CITY}, ${BUSINESS_STATE}`;
export const INVENTORY_DESCRIPTION =
  `See the current manufactured and mobile home inventory at ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}, `
  + 'including listed homes, pre-owned homes, and floorplans.';

export function cityTitle(city) {
  return `Manufactured Homes in ${city}, TX | ${BUSINESS_NAME}`;
}

export const LOCATIONS_TITLE = `Locations | ${BUSINESS_NAME}`;
export const LOCATIONS_DESCRIPTION =
  'Town pages around the Huffman showroom, each with its county and direction from that lot, plus links to the other towns and the home inventory.';

export function cityShopperSentence(city) {
  return (
    `Homes for shoppers in ${city}, TX at ${BUSINESS_NAME}, ${BUSINESS_FULL_ADDRESS}. `
    + `${DELIVERY_LINE} Call ${BUSINESS_PHONE}.`
  );
}

export function cityDescription(city) {
  return cityShopperSentence(city);
}

export function categoryTitle(classification) {
  return `${classification} Homes in ${BUSINESS_CITY}, ${BUSINESS_STATE} | ${BUSINESS_NAME}`;
}

export function categoryDescription(classification) {
  const label = String(classification || 'manufactured').toLowerCase();
  return (
    `Browse ${label} manufactured and mobile homes at ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}. `
    + `Listed homes and orderable floorplans. Call ${BUSINESS_PHONE} for price.`
  );
}

// Per-home listing titles. Keep in step with seo_routes._listing_document_title.
// City and state drop first, then a trailing stock code, then a long series
// banner. A model name that still does not fit is cut on a word boundary.
const LISTING_TITLE_LIMIT = 60;
const LISTING_DESCRIPTION_LIMIT = 155;
const ELLIPSIS = '…';

function collapseWs(value) {
  return String(value || '').split(/\s+/).filter(Boolean).join(' ');
}

function clipOnWord(value, limit) {
  const text = collapseWs(value);
  if (limit <= 0) return '';
  if (text.length <= limit) return text;
  if (limit === 1) return ELLIPSIS;
  const budget = limit - ELLIPSIS.length;
  let cut = text.slice(0, budget).trimEnd();
  const space = cut.lastIndexOf(' ');
  if (space >= Math.max(8, Math.floor(budget / 2))) {
    cut = cut.slice(0, space);
  }
  cut = cut.replace(/[\s\-–—/|,;:]+$/u, '');
  if (!cut) {
    cut = text.slice(0, budget).replace(/[\s\-–—/|,;:]+$/u, '') || text.slice(0, budget);
  }
  return `${cut}${ELLIPSIS}`;
}

function withoutTrailingCodes(text) {
  const words = collapseWs(text).split(' ');
  let changed = false;
  while (words.length > 1 && /\d/.test(words[words.length - 1])) {
    words.pop();
    changed = true;
  }
  if (!changed) return '';
  return words.join(' ').replace(/[\s\-–—/|,;:]+$/u, '');
}

function fitListingName(name, limit) {
  const clean = collapseWs(name);
  if (clean.length <= limit) return clean;
  const trimmed = withoutTrailingCodes(clean);
  if (trimmed && trimmed.length <= limit) return trimmed;
  const source = trimmed || clean;
  if (source.includes('/')) {
    const segment = source.split('/').pop().trim().replace(/[\s\-–—/|,;:]+$/u, '');
    if (segment && segment.length <= limit) return segment;
  }
  return clipOnWord(source, limit);
}

export function listingDocumentTitle(modelName) {
  const name = collapseWs(modelName) || 'Manufactured Home';
  const brand = BUSINESS_NAME;
  const withPlace = `${name} — ${brand}, ${BUSINESS_CITY}, ${BUSINESS_STATE}`;
  if (withPlace.length <= LISTING_TITLE_LIMIT) return withPlace;
  const suffix = ` — ${brand}`;
  if (suffix.length < LISTING_TITLE_LIMIT && name.length + suffix.length <= LISTING_TITLE_LIMIT) {
    return `${name}${suffix}`;
  }
  if (suffix.length >= LISTING_TITLE_LIMIT) return clipOnWord(`${name}${suffix}`, LISTING_TITLE_LIMIT);
  return `${fitListingName(name, LISTING_TITLE_LIMIT - suffix.length)}${suffix}`;
}

export function listingMetaDescription(modelName, bits) {
  const name = collapseWs(modelName) || 'Manufactured home';
  const tail = `at ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}. Call ${BUSINESS_PHONE}.`;
  const kept = (Array.isArray(bits) ? bits : [])
    .map((bit) => String(bit || '').trim())
    .filter(Boolean);
  while (kept.length) {
    const description = `${name} (${kept.join(', ')}) ${tail}`;
    if (description.length <= LISTING_DESCRIPTION_LIMIT) return description;
    kept.pop();
  }
  const bare = `${name} ${tail}`;
  if (bare.length <= LISTING_DESCRIPTION_LIMIT) return bare;
  const room = LISTING_DESCRIPTION_LIMIT - tail.length - 1;
  if (room < 1) return clipOnWord(bare, LISTING_DESCRIPTION_LIMIT);
  return `${clipOnWord(name, room)} ${tail}`;
}

export const PUBLIC_DESCRIPTIONS = {
  contact:
    `Visit ${BUSINESS_NAME}, ${BUSINESS_FULL_ADDRESS}. Call ${BUSINESS_PHONE}. `
    + 'Hours: Mon-Fri 9:00 AM - 6:00 PM, Sat 10:00 AM - 3:00 PM, Sun Closed.',
  appointments:
    `Schedule a visit to the ${BUSINESS_NAME} showroom in ${BUSINESS_CITY}, ${BUSINESS_STATE} `
    + 'to tour manufactured homes in person.',
  chat:
    `Ask Tex, the ${BUSINESS_NAME} assistant, about manufactured homes, pricing, and availability `
    + `in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  about:
    `Learn about ${BUSINESS_NAME}, a licensed manufactured and mobile home dealership in `
    + `${BUSINESS_CITY}, ${BUSINESS_STATE} serving the Houston area.`,
  financing: FINANCING_LINE,
  faq:
    `Answers about manufactured homes, financing, delivery, warranty, and buying from `
    + `${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  warranty:
    `${WARRANTY_LINE} ${BUSINESS_NAME} can walk through the paperwork for the home you are considering.`,
  delivery:
    `${DELIVERY_LINE} ${BUSINESS_NAME} can talk through transport, placement, and site preparation for a specific address.`,
  privacy: legalPages.privacy.description,
  terms: legalPages.terms.description,
  'not-found': `The page you requested does not exist. Browse homes for sale at ${BUSINESS_NAME}.`,
  locations: LOCATIONS_DESCRIPTION,
};
