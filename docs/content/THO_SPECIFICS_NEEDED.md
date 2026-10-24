# Texas Home Outlet — business specifics for staff

> **Status:** Answered from records already in this repo. Anything still open says **Ask your manager.**
> **Last updated:** 2026-10-23

This note replaces the old questionnaire. It does not add prices, lender names, license numbers, warranty terms, or delivery promises that the repo does not already record.

The public FAQ page is `frontend/src/pages/FAQ.jsx`. Hours in `docs/content/FAQ.md` must keep matching `config.yaml`.

---

## 1. Business identity

- **Tagline:** "Your Dream Home, Our Mission" — `config.yaml` `business.tagline`.
- **Founding story and biography:** not recorded. `config.yaml` `business.year_established` is empty. Ask your manager.
- **People named in the handoff:** the site walkthrough lists day-to-day users as Ben, Lee, Celeste, and Mark (`docs/CLIENT_WALKTHROUGH.md`). Lead-alert addresses in `config.yaml` `business.notification_email` are ben@texashomeoutlet.com, leeaswell@texashomeoutlet.com, celeste@texashomeoutlet.com, mark@texashomeoutlet.com, and adriana@texashomeoutlet.com. Headcount and job titles are not recorded. Ask your manager.
- **What makes the store different from another dealer:** not recorded. Ask your manager.
- **Cities already listed as served:** Huffman, Humble, Atascocita, Crosby, Kingwood, New Caney, Baytown, Beaumont, Cleveland, Conroe, and Livingston — `config.yaml` `business.area_served` (same list in `frontend/src/constants.js` `SERVICE_AREA_CITIES`).

## 2. Financing

Lender names, down payments, credit minimums, land-home terms, sample payments, and approval times are not recorded. `config.yaml` `lenders` leaves creditor address and phone blank, and the comment there says that paperwork was not found in the repo.

Ask your manager. Do not name a lender or an in-house program.

## 3. Home delivery and setup

What delivery includes, any delivery fee, any radius beyond the city list above, setup crew, and who pulls permits are not recorded. `docs/team/city-pages-overlap.md` keeps the existing city sentence and does not add per-city fees.

Ask your manager. Do not promise a delivery time or a delivery fee.

## 4. Warranty and after-sale support

Warranty length, any store warranty beyond the manufacturer packet, and the service process are not recorded.

Ask your manager. Do not state a warranty length.

## 5. Manufacturers and inventory

- **Which names to give a customer:** not recorded as a full approved list. `frontend/src/constants.js` says `FACTORY_OPTIONS` is a seed list for the document form and that the dealership has not supplied the full list. Ask your manager.
- **Dollar price range:** not recorded. `config.yaml` `business.price_range` is the schema.org symbol `$$`, not a dollar range. Public ads stay **Call for Price** unless the owner or operator has approved a number (`docs/team/ad-studio-guide.md`). A manager who is not the owner or operator cannot approve that number.
- **Pre-owned homes:** the site has a used-home closing packet (`docs/CLIENT_WALKTHROUGH.md`) and a used-home photo rule (`docs/team/staff-gotchas.md`). How many are usually in stock is not recorded. Ask your manager.
- **Floor-plan customization:** not recorded. Ask your manager.

## 6. Hours and contact

- **Hours:** Mon-Fri 9:00 AM - 6:00 PM, Sat 10:00 AM - 3:00 PM, Sun Closed — `config.yaml` `business.hours`.
- **Visits:** the public FAQ says customers may visit the Huffman showroom during those hours or book a showroom visit (`frontend/src/pages/FAQ.jsx`).
- **Phone:** (281) 324-3020 — `config.yaml` `business.phone`.
- **Address:** 10685 FM 1960 East, Huffman, TX 77336 — `config.yaml` `business.address`.
- **Email:** sales@texashomeoutlet.com — `config.yaml` `business.email`.
- **Profiles already listed:** Google Maps search link, https://www.facebook.com/texashomeoutlet/, https://www.yelp.com/biz/texas-home-outlet-east-huffman-2, and the BBB Huffman profile — `config.yaml` `business.same_as`. Instagram and TikTok links are not in that list. Ask your manager.

## 7. Customer proof

Testimonials, customer photos, video testimonials, and a review quota are not recorded.

Ask your manager. Do not invent a customer story.
