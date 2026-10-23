# Admin, Ads, and AI Ads Studio check

Checked on 2026-10-23. This extends the Ad Studio click path from PRs #428 and #429 into one path: staff sign-in, listing admin, Ads, and AI Ads Studio.

Local runs use test fixtures and mocked API responses. The fixture PIN is the string `clickpath-fixture-pin`. It is not a store secret. The live run opens https://www.texashomeoutlet.com and does not type, submit a form, or send admin, inventory, or ad writes.

## Local results

Vitest rendered the app in jsdom with mocked `fetch`. Playwright opened the Vite app at http://127.0.0.1:5173 with `CLICKPATH_MOCKS=1` and fulfilled API calls in the browser. Both passed the signed-in path.

| Step | What was checked | Vitest | Playwright |
| --- | --- | --- | --- |
| 1. Staff sign-in page | `/staff` shows the Staff sign-in heading and the work email field | pass | pass |
| 2. Fixture sign-in | Backup PIN accepts only the fixture string and opens Staff Home | pass | pass |
| 3. Listing admin | Add or change a home opens `/manage-inventory`, shows Clickpath Single, opens the editor, and closes it with Done. No home save | pass | pass |
| 4. Ads | Staff menu Google Ads opens `/ad-studio` on 1. Create. The check and launch buttons were not pressed | pass | pass |
| 5. AI Ads Studio | Staff menu Ad Studio opens `/studio` on Create Ad, then Choose a home selects Clickpath Single and leaves Price empty. Generate and Prepare Draft were not pressed | pass | pass |

Signed-out live steps are not part of the mocked signed-in run.

## Live results

Playwright loaded https://www.texashomeoutlet.com with `CLICKPATH_MOCKS` unset. Every request other than GET and HEAD was aborted. No field was filled.

| Step | What was checked | Result |
| --- | --- | --- |
| 1. Staff sign-in page | GET `/staff` shows Staff sign-in and an empty work email field | pass |
| 2. Fixture sign-in | Would submit a PIN | not run |
| 3. Listing admin while signed in | Would open the home editor | not run |
| 4. Signed-out listing admin | GET `/manage-inventory` shows Staff sign-in. Homes on the website and Add a home are absent | pass |
| 5. Ads while signed in | Would open 1. Create | not run |
| 6. Signed-out Ads | GET `/ad-studio` shows the staff gate. 1. Create and Tex's Ad Studio are absent | pass |
| 7. AI Ads Studio while signed in | Would open Create Ad and choose a home | not run |
| 8. Signed-out AI Ads Studio | GET `/studio` shows the staff gate. Choose a home, Generate Script, and Tex's Ad Studio are absent | pass |
| 9. Read-only network | 4 `POST /api/chat/session` calls were aborted. None were admin verify, inventory, or ad writes | pass |

The public app shell starts a chat session when these pages load. This check aborted those posts, so they did not complete.

## Bug fixed

Opening Google Ads at `/ad-studio` and then choosing Ad Studio changed the address to `/studio` but left the Google Ads tab selected. Ads and AI Ads Studio share one screen, and the screen only followed the address when the entry was Google Ads.

`AdStudio.jsx` now follows the address for both `/ad-studio` (Google Ads) and `/studio` (Create Ad). A tab click inside the page still stays on that tab. The local step 5 assertions cover the fix.

## Commands

```sh
cd frontend && npx vitest run src/__tests__/AdminAdsClickPath.test.jsx
cd e2e && CLICKPATH_MOCKS=1 BASE_URL=http://127.0.0.1:5173 npx --no-install playwright test tests/admin-ads-clickpath.spec.js
cd e2e && BASE_URL=https://www.texashomeoutlet.com npx --no-install playwright test tests/admin-ads-clickpath.spec.js
```

`npm test` in `e2e` stays read-only unless `CLICKPATH_MOCKS=1` and `BASE_URL` is a loopback address.
