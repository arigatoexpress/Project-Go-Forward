export const INVENTORY_CATEGORY_ROUTES = Object.freeze({
  '/single-wide': Object.freeze({
    path: '/single-wide',
    classification: 'Single Wide',
    heading: 'Single Wide Manufactured Homes in Huffman, TX',
    description: 'Browse single wide listed homes plus manufacturer floorplans Texas Home Outlet can custom order. Confirm current price and availability with our team.',
  }),
  '/double-wide': Object.freeze({
    path: '/double-wide',
    classification: 'Double Wide',
    heading: 'Double Wide Manufactured Homes in Huffman, TX',
    description: 'Browse double wide listed homes plus manufacturer floorplans Texas Home Outlet can custom order. Confirm current price and availability with our team.',
  }),
});

function normalizePath(pathname) {
  const raw = String(pathname || '/').toLowerCase();
  return raw.length > 1 ? raw.replace(/\/+$/, '') : raw;
}

const CLASSIFICATION_ALIASES = Object.freeze({
  'single wide': 'Single Wide',
  singlewide: 'Single Wide',
  'single section': 'Single Wide',
  singlesection: 'Single Wide',
  'double wide': 'Double Wide',
  doublewide: 'Double Wide',
  'double section': 'Double Wide',
  doublesection: 'Double Wide',
  'multi section': 'Double Wide',
  multisection: 'Double Wide',
  'park model': 'Park Model',
  parkmodel: 'Park Model',
  'manufactured home': 'Manufactured Home',
  manufacturedhome: 'Manufactured Home',
});

export function normalizeInventoryClassification(value) {
  const trimmed = String(value || '').trim();
  if (!trimmed) return '';
  const aliasKey = trimmed.replace(/[\s_-]+/g, ' ').trim().toLowerCase();
  return CLASSIFICATION_ALIASES[aliasKey] || trimmed;
}

export function getInventoryCategoryRoute(pathname) {
  return INVENTORY_CATEGORY_ROUTES[normalizePath(pathname)] || null;
}

export function isInventoryCategoryPath(pathname) {
  return getInventoryCategoryRoute(pathname) !== null;
}
