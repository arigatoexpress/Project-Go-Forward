import {
  BUSINESS_CITY,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_HOURS,
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
// Titles stay within a normal search-result length. Descriptions say what the
// page is, using the phone, address, and hours already shown on the site.

export const HOME_TITLE = `Manufactured Homes for Sale in ${BUSINESS_CITY}, ${BUSINESS_STATE} | ${BUSINESS_NAME}`;
export const HOME_H1 = `Manufactured and Mobile Homes for Sale in ${BUSINESS_CITY}, ${BUSINESS_STATE}`;
export const HOME_DESCRIPTION =
  `Browse manufactured and mobile homes for sale at ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}. `
  + `Call or text ${BUSINESS_PHONE} to confirm price, financing, and availability.`;

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
    `${BUSINESS_NAME} lists manufactured and mobile homes for shoppers in ${city}, TX `
    + `from the ${BUSINESS_CITY} showroom at ${BUSINESS_FULL_ADDRESS}. `
    + `${DELIVERY_LINE} Call ${BUSINESS_PHONE} for the current price.`
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

export const PUBLIC_DESCRIPTIONS = {
  contact:
    `Visit ${BUSINESS_NAME} at ${BUSINESS_FULL_ADDRESS}, or call ${BUSINESS_PHONE}. `
    + `Hours: ${BUSINESS_HOURS}.`,
  appointments:
    `Schedule a visit to the ${BUSINESS_NAME} showroom in ${BUSINESS_CITY}, ${BUSINESS_STATE} `
    + 'to tour manufactured homes in person.',
  chat:
    `Ask Tex, the ${BUSINESS_NAME} assistant, about manufactured homes, pricing, and availability `
    + `in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  about:
    `Learn about ${BUSINESS_NAME}, a licensed manufactured and mobile home dealership in `
    + `${BUSINESS_CITY}, ${BUSINESS_STATE} serving the Houston area.`,
  financing:
    `${FINANCING_LINE} ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  faq:
    `Answers about manufactured homes, financing, delivery, warranty, and buying from `
    + `${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  warranty:
    `${WARRANTY_LINE} ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  delivery:
    `${DELIVERY_LINE} ${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  privacy: legalPages.privacy.description,
  terms: legalPages.terms.description,
  'not-found': `The page you requested does not exist. Browse homes for sale at ${BUSINESS_NAME}.`,
  locations: LOCATIONS_DESCRIPTION,
};
