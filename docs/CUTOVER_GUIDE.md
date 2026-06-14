# Texas Home Outlet — Website & Email Cutover Guide

**Status as of 2026-06-14:** Website **live** on the real domain. Customer email in **final verification** (auto-completing). Plain-English guide for the team.

---

## What changed (in one paragraph)
The website moved from its temporary address to its real name, **https://www.texashomeoutlet.com**, running on Google's cloud with a valid security certificate. Search rankings were preserved (old links redirect, page titles + structured data carry over). We also set up professional customer email from the domain. Inbound email to `@texashomeoutlet.com` still goes to Yahoo exactly as before — that did not change.

## Where everything lives now
| Piece | Provider | Notes |
|---|---|---|
| Website | Google Cloud Run (`project-go-forward`, project `tho-ai-agent`) | apex + `www`, auto-SSL |
| DNS (the domain's address book) | **Google Cloud DNS** (zone `texashomeoutlet-com`) | moved here from Turbify so we could add the email records Turbify couldn't |
| Outbound/customer email | **Resend** (sending domain `texashomeoutlet.com`) | DKIM/SPF/DMARC authenticated |
| Inbound email | **Yahoo / Turbify** (unchanged) | `@texashomeoutlet.com` mailboxes untouched |

## How to verify it's working (anyone can do this)
1. Open **https://www.texashomeoutlet.com** — the real site should load with a padlock (secure).
2. Open **https://texashomeoutlet.com** (no www) — also loads securely.
3. Send/receive a normal email to your `@texashomeoutlet.com` address — inbound mail is unchanged (Yahoo).
4. A **daily automated health check** now runs in the cloud (site + DNS + email status) and reports any problems.

## If something looks wrong — rollback
DNS can be reverted in minutes: change the domain's **nameservers** back to `ns1.turbify.com` / `ns2.turbify.com`. The website would then resolve through Turbify again. (We don't expect to need this — the new setup mirrors the old one and only *adds* the missing email record.)

## What's still in progress / pending
- **Customer email (Resend):** final domain verification is propagating and completes automatically. Once green, transactional emails (contact form, appointments) send from `noreply@texashomeoutlet.com`.
- **Marketing/analytics:** GA4 / Meta / TikTok tracking is built and waiting on account IDs + a cookie-consent decision before turn-on.
- **Code upgrades:** a batch of site improvements (SEO bug fix, analytics wiring, social-share cards, CRM hardening, rate-limiting) is built and tested (786 automated tests pass), staged for review + deploy.

## Who did what
- **DNS / nameserver change, email records:** Ari (with the cloud agent staging everything).
- **Hosting, SSL, SEO, code:** automated build + cloud agent.
- **Inbound mailboxes:** Yahoo/Turbify — untouched.

## Key facts to keep handy
- Live site: `https://www.texashomeoutlet.com`
- Rollback nameservers: `ns1.turbify.com`, `ns2.turbify.com`
- Sending email address: `noreply@texashomeoutlet.com` (replies → `support@texashomeoutlet.com`)
- Nothing about **receiving** email changed.
