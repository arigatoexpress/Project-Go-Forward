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

The suite checks home, inventory with a loaded card image, a plan discovered
from the sitemap, contact fields, the initial appointment calendar shell,
health JSON with a nonempty version, privacy, and terms. It never fills or
submits a form or progresses the appointment wizard. Browser requests other
than GET and HEAD are aborted, including analytics; service workers are blocked.
API checks use GET only. No credentials or production secrets are required.

Privacy and terms use `test.fail` until Task E merges and reaches the tested
target. An unexpected pass fails the suite so the owner knows to remove those
two annotations. Their failures remain visible in the report. Browser traces,
screenshots, and videos are disabled.
