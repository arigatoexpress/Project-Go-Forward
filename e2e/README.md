# Read-only storefront smoke

Run the `Read-only storefront smoke` workflow manually with a storefront origin
in `BASE_URL`. The default is the Cloud Run candidate. There are no push, pull
request, or scheduled triggers and this workflow does not deploy anything.
GitHub enables manual dispatch after the workflow exists on the default branch.

Local execution:

```sh
cd e2e
npm ci
npx --no-install playwright install chromium
BASE_URL=http://127.0.0.1:8080 npm test
```

`tests/smoke.spec.js` checks home, inventory with a loaded card image, a plan
discovered from the sitemap, contact fields, the initial appointment calendar
shell, health JSON with a nonempty version, privacy, and terms. It never fills
or submits a form or progresses the appointment wizard. Browser requests other
than GET and HEAD are aborted, including analytics; service workers are blocked.
API checks use GET only. No credentials or production secrets are required.

`tests/admin-ads-clickpath.spec.js` checks the signed-out staff, listing, Ads,
and AI Ads Studio pages. It does not fill or submit a form. Set
`CLICKPATH_MOCKS=1` with a loopback `BASE_URL` to walk the signed-in path
against mocked API responses and a test fixture PIN. That mode does not run
unless both are set, so `npm test` against a real server stays read-only.

Privacy and terms are asserted directly. Those pages are on `main`. Browser
traces, screenshots, and videos are disabled.
