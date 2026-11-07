# Storefront scan L1-56 (2026-11-07)

Read-only scan of the live Texas Home Outlet storefront. Nothing was promoted. Traffic was not changed. Passkey, IAM, DNS, secrets, and auth enrollment code were not edited.

## Live identity

- `GET https://www.texashomeoutlet.com/healthz/` returned status ok and version `a9dd8221` (the full git revision of that deploy).
- That commit is the serving storefront (`Show a dismissible Tex chat prompt on phone-sized storefronts`, #495).
- `main` at scan time was `87d812c8` (#496 passkeys and #497 logging). Those commits do not change the listing display files this fix touches. The draft PR is based on `main` so it does not revert them. It is a draft and is not a promotion request.

## What was checked

- Sitemap: 283 URLs, plus `/chat`, `/privacy`, `/terms`, `/contact`, `/locations`, `/about`, `/financing`, `/faq`, `/warranty`, `/delivery`, `/appointments`, `/single-wide`, `/double-wide`, `/inventory`, `/manufactured-homes-in-houston-tx`, `/plan/226545/solution/the-absolute-value-slc28764a/`, `/legal`, `/cavco-homes-of-texas/`, `/quote/1`, and a missing stock URL. 287 fetches.
- Public inventory JSON (`/api/marketing/inventory-context`): 278 homes.
- Rendered DOM of `/` via headless Chrome, plus screenshots of `/` (desktop and 390px), `/single-wide`, `/plan/226545/solution/the-absolute-value-slc28764a/`, `/manufactured-homes-in-houston-tx`, and `/chat` at phone width.
- Checklist: placeholders, TODO/FIXME, lorem, broken in-page links, missing title/description/h1/canonical, dollar amounts, Call for Price, Privacy and Terms.

## Findings

### 1. Plan pages omit square feet in crawlable HTML — medium — fixed in this PR

- URL: `https://www.texashomeoutlet.com/plan/226545/solution/the-absolute-value-slc28764a/`
- The live crawlable list shows beds, baths, dimensions, manufacturer, and Call for Price. It does not show square feet.
- Every public home stores footage on `specs.sq_ft` (278/278). The plan template only read `specs.sqft`.
- The React page does show it. The Absolute Value screenshot reads `4 bed · 2 bath · 1,934 sq ft` and `Call for Price`.
- Fix: print `sq_ft` on plan pages the same way in-stock `/homes/` pages already do.

### 2. Crawlable bath counts print as 2.0 — low — fixed in this PR

- URL: `https://www.texashomeoutlet.com/single-wide` (93 times) and the Absolute Value plan page (`Baths: 2.0`).
- Orderable floorplans store baths as a float. `2.0` was printed as `2.0 bath`. `2.5` must stay `2.5` (2 homes).
- The React cards already show `2 bath`. Search snippets and non-JS HTML showed `2.0`.
- Fix: print whole counts as `2`.

### 3. One listing shows the manufacturer as Unknown — low — fixed in this PR

- URL: `https://www.texashomeoutlet.com/` and `https://www.texashomeoutlet.com/single-wide`
- Home: The Big Deal (available now, single wide). `manufacturer` is the word `Unknown`. The rendered homepage DOM shows that word under the name. The single-wide crawlable line ends with `· Unknown`.
- No other public home uses a placeholder factory name. `Pre-Owned` is left as-is.
- Fix: omit placeholder factory labels (`Unknown`, `N/A`, `TBD`, and the same kind of token). Do not invent a manufacturer and do not substitute the dealership name for that token.

### 4. Catalog blurbs are cut off mid-word — low — not changed

- 221 of 278 homes have a description that already ends in `...`, often mid-word. Absolute Value’s crawlable paragraph ends `manufactured home offerin...`.
- That text is source catalog copy. Completing it would invent a sentence. The React listing summary does not use that paragraph; it uses the spec line and Call for Price.
- Proposed later fix, if wanted: drop a trailing incomplete word in the crawlable paragraph, or stop publishing the stored teaser. Not done here.

### 5. Phone chat card covers the first screen until dismissed — low — not changed

- On a 390px screenshot the dismissible “Chat with Tex” card sits over the search field on `/`, over town links on `/single-wide`, and over the Houston paragraph.
- The close control is visible. This card is the storefront prompt shipped in `a9dd8221` (#495). It was not redesigned in this pass.

## Checked and clean

- No user-visible TODO, FIXME, lorem, CHANGEME, or TBD on the 287 fetched public pages.
- All 278 listings have `display_price` of `Call for Price`. No dollar amount in those pages. The React home and Absolute Value screenshots say Call for Price.
- Privacy Policy and Terms of Use are real pages (`/privacy`, `/terms`) with titles, descriptions, and body copy. The chat footer links to both. `/legal` canonicalizes to `/privacy`.
- Key routes returned 200 with a title, meta description, h1, and canonical: home, chat, inventory, Houston, Absolute Value, single-wide, double-wide, locations, about, financing, FAQ, warranty, delivery, contact, appointments.
- Crawlable in-page links on those pages pointed at URLs in the same fetch set. A made-up stock URL returned 410, “This home is no longer listed.”
- `/cavco-homes-of-texas/` and an unknown `/quote/1` redirect to inventory (301). They are legacy routes, not soft 404s.
- Staff-uploaded listing photo URLs that are relative (`/api/inventory/photos/...`) returned 200 (18 checked). The empty `alt` on the homepage LCP image is `aria-hidden`.
- Houston, chat, contact, and the legal pages render real copy. Chat shows the Tex prompt, quick actions, and the phone. No staff page was opened.

## Not treated as a storefront defect

- Matterport `show` URLs returned 403 to this scanner’s user agent. Tours are still linked from the pages. That 403 was not used as a reason to remove tours.
- Neutral lines such as “Delivery available, ask for details” and “Warranty coverage varies by manufacturer, ask a manager” are the published copy for facts the dealership has not posted. They were not replaced with invented details.
