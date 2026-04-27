# Frontend Testing Proposal

Status: DRAFT — proposal only, no test framework added yet
Owner: TBD (frontend lead)
Last updated: 2026-04-27

## Why this doc exists

The THO backend gained substantial test coverage in PRs #20, #21, #22 today.
The frontend (`frontend/src/`, ~250k LOC across pages and components) has
**zero automated tests**. There is no test runner, no `__tests__/` directory,
no `*.test.*` / `*.spec.*` file in the repo, and no test script in
`frontend/package.json`.

Adding a test framework is a non-trivial decision (new dev dependency, CI
wiring, conventions, owner). This doc proposes an approach so the team can
review and approve it before any framework is installed.

## Current frontend stack (for context)

From `frontend/package.json` and `frontend/vite.config.js`:

- React `^19.2.0` (function components, hooks)
- Vite `^7.2.4` build tool
- Tailwind `^4.1.18` styling
- ESLint `^9.39.1` for lint
- Lucide / Recharts / Framer Motion for UI
- No test runner, no jsdom, no testing-library, no MSW

The largest production-adjacent surfaces (and their LOC) are:

| Surface | File | LOC | Risk |
|---|---|---|---|
| Document Center | `frontend/src/pages/DocumentCenter.jsx` | ~2,170 | HIGH — drives PDF generation, customer lookup, deal load |
| Ad Studio | `frontend/src/pages/AdStudio.jsx` | ~1,600+ | HIGH — public marketing surface |
| Inventory Browse | `frontend/src/pages/InventoryBrowse.jsx` | ~1,084 | HIGH — public buyer-facing inventory |
| CRM | `frontend/src/pages/CRM.jsx` | ~1,300+ | MEDIUM — internal staff |
| Analytics | `frontend/src/pages/Analytics.jsx` | ~900 | LOW — internal staff, read-only |
| Appointments | `frontend/src/pages/Appointments.jsx` | ~520 | MEDIUM |
| ErrorBoundary | `frontend/src/components/ErrorBoundary.jsx` | small | HIGH — guards every page |

## Recommended setup

### 1. Test runner: Vitest

**Why Vitest** (over Jest):

- Native Vite integration — reuses the same `vite.config.js`, plugins, and
  transformer, so JSX / Tailwind / aliases just work
- Jest-compatible API (`describe`, `it`, `expect`, `vi.mock`) so existing
  React Testing Library docs all apply unchanged
- ESM-native (Jest still has rough edges with React 19 + ESM)
- Fast watch mode powered by Vite HMR
- Owned by the Vite team, so it tracks Vite ^7 directly

### 2. DOM environment: `@testing-library/react` + `jsdom`

- `@testing-library/react` for component rendering
- `@testing-library/jest-dom` for `toBeInTheDocument`, `toHaveTextContent`, etc.
- `@testing-library/user-event` for realistic user interactions
- `jsdom` for the DOM (Vitest bundles it as an opt-in env)

### 3. Network mocking: `vi.spyOn(globalThis, 'fetch')` for v1

For the first wave of tests, stubbing `fetch` directly is enough — most
THO API calls are simple GET/POST against `/api/...`. If we later need to
test more elaborate request flows, upgrade to MSW (`msw@^2`) which works
in jsdom and Vitest with no extra config.

**No MSW for v1** — keeps the dependency footprint to four packages.

### 4. Proposed dev dependencies (4 packages)

```json
{
  "devDependencies": {
    "vitest": "^2.1.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/user-event": "^14.5.0",
    "jsdom": "^25.0.0"
  }
}
```

Five packages total, all maintained by the Testing Library team or the
Vite team. None pull in Babel or other heavy transitive deps.

### 5. Proposed `vite.config.js` addition

```js
// add inside defineConfig({...})
test: {
  environment: 'jsdom',
  globals: true,
  setupFiles: ['./src/test/setup.js'],
  css: false,                  // skip Tailwind in unit tests
  include: ['src/**/*.test.{js,jsx}'],
}
```

And `src/test/setup.js`:

```js
import '@testing-library/jest-dom/vitest';
```

### 6. Proposed `package.json` scripts

```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "test:ui": "vitest --ui"
  }
}
```

### 7. CI wiring

Add to the existing GitHub Actions workflow (the same job that already
runs `pre-commit` and pytest). One new step:

```yaml
- name: Frontend tests
  working-directory: frontend
  run: npm ci && npm test
```

No matrix changes. Total CI time impact estimated <60s for the first
~20 tests.

## First wave of tests once approved (4-8 tests)

If approved, the recommended first surface is **InventoryBrowse**
(public-facing, recently touched by PR #19, mid-size at 1,084 LOC). The
initial test file `frontend/src/pages/__tests__/InventoryBrowse.test.jsx`
would cover:

1. Renders the page without crashing on mount
2. Shows the loading spinner while `/api/inventory` is pending
3. Shows the empty state when `/api/inventory` returns `[]`
4. Shows an error message when `/api/inventory` rejects (covers ErrorBoundary path)
5. Renders a `HomeCard` for each inventory item returned
6. Search input filters the visible cards
7. Sort dropdown reorders cards (price low → high)
8. Clicking a `HomeCard` opens the `HomeDetailModal`

These are pure component tests against a mocked `fetch` — no Firestore,
no Cloud Run, no real network.

## What this proposal does NOT do

- Does NOT install any new dependency (review-only)
- Does NOT change `package.json`, `vite.config.js`, or CI
- Does NOT add any test files
- Does NOT touch `tho_documents/`, `database/models.py`,
  `database/deal_validation.py`, `tools/inventory_sync.py`, or
  `main.py` (those are owned by PRs #20-#22)

## Decision needed

Approve / reject / amend the framework choice (Vitest vs Jest vs Playwright
component testing vs none-for-now). Once approved, a follow-up PR will:

1. Add the 5 dev dependencies
2. Add the `test` config block to `vite.config.js`
3. Add `src/test/setup.js`
4. Add the `test` / `test:watch` scripts
5. Add the first 4-8 component tests for InventoryBrowse
6. Wire the GitHub Actions step

That follow-up PR will be a single coherent change reviewable in one pass.

## Alternatives considered

- **Jest + react-testing-library** — workable but requires its own
  Babel/ESM config separate from Vite. Adds friction every time the Vite
  config changes.
- **Playwright component testing** — heavier (real browser), better for
  E2E than unit-level component tests. Worth adding *later* for
  end-to-end smoke tests of Document Center PDF download flow, but
  overkill for the first wave.
- **Cypress component testing** — similar to Playwright; also heavier
  than Vitest + jsdom.
- **Do nothing** — leaves the public-facing buyer surface and the
  document-generation flow with zero regression coverage. Not
  recommended given backend test investment.

## Effort estimate

| Step | Effort |
|---|---|
| Approval discussion | 1 review cycle |
| Install + config PR | ~1 hour |
| First InventoryBrowse test file (8 tests) | ~3 hours |
| CI wiring + green build | ~30 min |

Total: ~half a day of focused work after approval.
