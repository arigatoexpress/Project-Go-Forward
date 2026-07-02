// ─── Shared Business Constants ───
// Single source of truth — import from here instead of hardcoding
export const BUSINESS_NAME = "Texas Home Outlet";
// Registered legal entity for all generated documents (per founding-partner
// directive 2026-05-18): "Prosperity Acquisitions, Inc. dba Texas Home Outlet"
// — no longer an LLC. Docs that don't name the correct INC entity get rejected.
export const BUSINESS_LEGAL_NAME = "Prosperity Acquisitions, Inc. dba Texas Home Outlet";
export const BUSINESS_SHORT = "tho";
export const BUSINESS_URL = "texashomeoutlet.com";
export const BUSINESS_PHONE = "(281) 324-3020";
export const BUSINESS_PHONE_RAW = "+12813243020";
export const BUSINESS_ADDRESS = "10685 FM 1960 East";
export const BUSINESS_CITY = "Huffman";
export const BUSINESS_STATE = "TX";
export const BUSINESS_ZIP = "77336";
export const BUSINESS_FULL_ADDRESS = `${BUSINESS_ADDRESS}, ${BUSINESS_CITY}, ${BUSINESS_STATE} ${BUSINESS_ZIP}`;
export const BUSINESS_LICENSE = "35248";
export const BUSINESS_HOURS = "Mon-Fri 9-6, Sat 9-5, Sun Closed";
export const NOTION_WORKSPACE_URL = "https://www.notion.so/Texas-Home-Outlet-34d6688dbec680f19371f6af10d1da15";

// ─── Document Center: constrained dropdown source lists ───
// Config-driven option lists for the manufacturing-factory and home-installer
// dropdowns on the document entry wizard (Mark Willcott review item A6). These
// are SEED values only — the dealership has not yet supplied the full list, so
// each list carries an explicit "THO to supply full list" placeholder and the
// Document Center still allows a free-typed value when "Other" is chosen.
// Replace/extend these arrays as THO confirms its approved factories/installers.
export const FACTORY_OPTIONS = [
  "CMH Manufacturing",
  "Clayton Homes",
  "Legacy Housing",
  "TRU Homes",
  "Southern Energy Homes",
];

export const INSTALLER_OPTIONS = [
  BUSINESS_LEGAL_NAME,
];

// ─── Site-wide store-closure banner ───
// Staff-editable holiday / closure notice shown across the public site.
// Dates are ISO `YYYY-MM-DD` (local). The banner shows through the END date
// (inclusive) and auto-hides once today is past `end` — no developer needed.
// Set STORE_CLOSURE to null to disable the banner entirely.
//   start  — first closed day (informational)
//   end    — last closed day; banner hides once today is past this date
//   reopen — first day open again (used in copy)
//   message — the exact line shown to visitors
export const STORE_CLOSURE = {
  start: '2026-07-02',
  end: '2026-07-07',
  reopen: '2026-07-08',
  message: 'Closed July 2–7 for the Independence Day holiday. We reopen July 8.',
};
