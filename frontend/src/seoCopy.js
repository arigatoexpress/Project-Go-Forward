import {
  BUSINESS_ADDRESS,
  BUSINESS_CITY,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_NAME,
  BUSINESS_PHONE,
  BUSINESS_STATE,
} from './constants';
import legalPages from './content/legalPages.json';

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

export function cityDescription(city) {
  return (
    `${BUSINESS_NAME} delivers manufactured and mobile homes to ${city}, TX from the ${BUSINESS_CITY} showroom `
    + `at ${BUSINESS_ADDRESS}. Call ${BUSINESS_PHONE} for price.`
  );
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
  financing:
    `Chattel, land-and-home, and FHA, VA, or USDA loans at ${BUSINESS_NAME} in `
    + `${BUSINESS_CITY}, ${BUSINESS_STATE}. Ask our team for current lender options.`,
  faq:
    `Answers about manufactured homes, financing, delivery, warranty, and buying from `
    + `${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  warranty:
    `Manufacturer warranties, component coverage, and service support for homes purchased from `
    + `${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  delivery:
    `Delivery, placement, utility connections, and site-prep guidance for manufactured homes from `
    + `${BUSINESS_NAME} in ${BUSINESS_CITY}, ${BUSINESS_STATE}.`,
  privacy: legalPages.privacy.description,
  terms: legalPages.terms.description,
  'not-found': `The page you requested does not exist. Browse homes for sale at ${BUSINESS_NAME}.`,
  locations: LOCATIONS_DESCRIPTION,
};
