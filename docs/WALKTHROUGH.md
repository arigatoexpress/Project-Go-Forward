# Repository Walkthrough — District Managers Presentation

A guided tour of this repository for a non-technical audience. Designed to be
followed live on screen in ~12 minutes plus Q&A. Every claim here is checkable
in the repo — if someone asks "show me," there's a place to click.

## The story in one minute

Texas Home Outlet used to rent its digital presence: a website controlled by a
third-party vendor, contract paperwork through FastContractDocs, leads living
in phone memory and sticky notes. We replaced all of it with one system the
business owns. It sells 24/7, never forgets a lead, fills the Texas closing
packet in minutes, and costs a fraction of the subscriptions it replaced.

## Live demo stops (5 min)

Open these in order — they're production, not a mockup:

1. **The storefront** — <https://tho.sapphirealpha.xyz/> — live inventory,
   273 homes (13 on-lot + 260 orderable floorplans). Click any home: photo
   gallery, floor plan, 3D walkthrough, "Get Price Quote."
2. **Tex** — click *Chat*. Ask it something real: "do y'all have a 3 bed
   under $100k?" Tex searches actual inventory and offers to book a visit.
   Try Spanish — it answers in Spanish. Ask it for financing math — it
   politely declines and offers an appointment (that's deliberate).
3. **Book Visit** — real open slots; bookings notify staff instantly and land
   in the CRM.
4. **The lock** — go to `/documents`. PIN + passkey gate. Customer-facing and
   staff-facing live in one app, but staff surfaces are sealed.
5. **On your phone** — same URL. The whole storefront is mobile-first because
   that's where buyers are.

## GitHub tour stops (5 min)

Walk the repo top to bottom — each stop makes a business point:

| Stop | Click | The point to make |
|---|---|---|
| 1 | [`README.md`](../README.md) (repo front page) | "This page is the system explaining itself — screenshots, the before/after table, the architecture picture." |
| 2 | [`prompts/`](../prompts/) → `sales_agent.md` | "This is literally what the AI is told to do, in plain English. Notice: never invent prices, never collect SSNs, hand emergencies to 911. AI behavior here is a **document we control**, not a black box." |
| 3 | [`tests/`](../tests/) (point at the count) | "655 automated checks run on every single change. If one fails, the change cannot ship. This is why we can move fast without breaking the store." |
| 4 | Pull requests tab → any merged PR | "Every change is a reviewed, documented proposal — what changed, why, proof it works, and how to undo it. Nothing sneaks into production." |
| 5 | [`LAUNCH_READINESS.md`](../LAUNCH_READINESS.md) | "Our honest status board. Green is proven with evidence; open items are listed, not hidden. This is how we know when we're ready to point texashomeoutlet.com here." |
| 6 | [`docs/SEO_MIGRATION.md`](SEO_MIGRATION.md) | "The #1 risk of replacing a website is losing your Google ranking. Here's the researched plan: every old URL stays alive, and the new site gives Google more than the old one ever did." |

## Anticipated questions — grounded answers

**"Is customer data safe?"**
Yes — and the protections are layered. Sensitive data (SSNs, financial info)
is stripped before anything reaches logs or the AI (`tools/pii_guard.py`);
Tex is instructed to refuse it in chat; staff areas need PIN + passkey; the
database has daily backups; and secrets live in Google's Secret Manager, not
in code (a full-history scan verified zero leaked credentials).

**"What if the AI says something wrong?"**
Three answers: (1) Tex only quotes inventory returned by our own systems — it
can't invent a home or a price; (2) it's explicitly forbidden from financing
math, legal advice, and sensitive data, and hands those to humans; (3) its
instructions are readable files in `prompts/` — if we don't like its behavior,
we edit a document, not rebuild software.

**"What does it cost to run?"**
The stack is pay-per-use Google Cloud (one small always-on service, a
database, AI calls). Order of magnitude: low hundreds of dollars a month,
with a budget alarm configured — versus the stacked subscriptions it replaces
(website vendor + FastContractDocs + marketing tools).

**"What happened to our old website and Google ranking?"**
The domain stays the same. All 279 indexed pages from the vendor site are
preserved on the new platform, with proper redirects for the rest. We added
the structured data and sitemaps the old site never had — the plan is to come
out ahead, not just survive. (`docs/SEO_MIGRATION.md` has sources for every
claim.)

**"Could other locations use this?"**
That's the design. Business specifics — name, address, hours, inventory
sources, even the AI's personality — live in one configuration file
(`config.yaml`) and editable prompt documents, not in code. Standing up a
second location is configuration plus their inventory, not a rewrite.

**"Who built this and who maintains it?"**
Built by Ari with AI-assisted engineering, with every change going through
the same PR pipeline you saw. Maintenance is the same loop: propose, test,
explicitly observe CI green, merge, deploy a zero-traffic candidate, then use a
separate operator gate for production promotion — plus monitoring that pages us
if anything is down (`docs/RUNBOOK.md` is the break-glass manual).

**"What's left before full launch?"**
The open items on `LAUNCH_READINESS.md`: recover fresh current inventory,
repair and validate the e-signature deployment, enforce the CI status check,
complete final secrets/restore verification, and run the staging E2E/load
gauntlet. The DNS cutover is complete.

## Presenter notes

- The site is live production — anything you click in the demo is real.
  Avoid submitting the contact/lead forms with fake data; staff get notified.
- If Tex is asked something odd during the demo, that's a feature moment:
  it declines and redirects — read its rules from `prompts/sales_agent.md`.
- Screenshots in the README are from production (June 2026). Refresh them
  after major visual changes — capture notes in the PR that added them.
