# SEO Migration Runbook — texashomeoutlet.com Cutover

How we move www.texashomeoutlet.com from the third-party vendor platform to
this Cloud Run service **without losing search equity** — and come out ahead.
Based on a sourced research pass (2026-06-10); citations at the bottom.

## Why the client should NOT panic (the short version)

1. **The domain isn't changing.** Same-domain replatforms keep domain-level
   trust; Google's Change-of-Address process doesn't even apply. The risk
   lives entirely in URL/content/metadata handling — all addressed below.
2. **301 redirects no longer lose PageRank** (Google, on the record, since
   2016). The old "15% loss per redirect" is obsolete.
3. **We kept the old URLs alive.** Every indexed legacy detail URL
   (`/inventory-detail/<id>/...`, `/plan/<id>/...`) serves 200 on the new
   platform with correct content — no redirect needed, zero equity risk for
   the pages that matter most.

## What the new platform now does (implemented in `seo_routes.py`)

| Surface | Behavior |
|---|---|
| Legacy detail URLs | Served 200 as the canonical URLs; per-home title, meta description, canonical, Open Graph, Product JSON-LD, crawlable HTML content |
| Legacy `/quote/...` URLs (290) | 301 to the matching home detail page (1:1 mapping from the cutover manifests); unknown quotes → `/inventory` |
| Legacy `/inventory/` hub | 301 → `/inventory` |
| `/`, `/inventory`, `/contact`, `/appointments`, `/chat` | Unique titles/descriptions ("...in Huffman, TX" pattern), self-referencing canonicals, OG tags, LocalBusiness JSON-LD, and a crawlable inventory block with `<a href>` links to every home (for non-JS crawlers: Bing + ~69% of AI crawlers don't execute JavaScript) |
| Admin routes (`/crm`, `/documents`, `/studio`, ...) | 200 + `noindex` robots meta |
| Unknown paths | Real HTTP 404 (kills the SPA soft-404 pattern) |
| `/robots.txt` | Allows everything crawlers need (assets + public APIs); disallows admin/PII endpoints; `Sitemap:` line. **No AI-crawler blocks — we want AI visibility** |
| `/sitemap.xml` | Static pages + every live home detail URL; no priority/changefreq (Google ignores them), no untruthful lastmod |
| `/llms.txt` | Buyer-facing rewrite; served with `X-Robots-Tag: noindex` (per Google guidance, so it doesn't rank as a page). Honest note: no major AI system consumes llms.txt today — it's kept because it's free, not because it's load-bearing |
| Structured hours | `config.yaml business.hours_structured` → schema.org `openingHoursSpecification` |

`CANONICAL_PUBLIC_URL` drives every absolute URL (canonicals, sitemap, OG).

## Cutover checklist (operator: Ari)

### Before DNS flips
1. **Search Console: verify a Domain property for texashomeoutlet.com via DNS**
   (survives any host change; URL-prefix verification can break at cutover).
2. Benchmark: export current Search Console performance (12 months) and note
   top pages/queries — this is the baseline for "did we lose anything."
3. Lower DNS TTL on www.texashomeoutlet.com ~1 week before cutover.
4. Crawl the vendor site one final time (archive in `data/legacy_site/`) in
   case any URL pattern emerged since the 2026-05 archive.
5. Update the Cloud Run service env: `CANONICAL_PUBLIC_URL=https://www.texashomeoutlet.com`,
   then map the custom domain in Cloud Run and provision the certificate.
6. Update `llms.txt` Site: line to the production domain.

### Cutover day
7. Flip DNS. Verify: `https://www.texashomeoutlet.com/healthz/` returns the
   expected version; `HEAD /` → 200; a legacy detail URL → 200 with correct
   title; a `/quote/...` URL → 301; `/nonexistent` → 404.
8. Submit `https://www.texashomeoutlet.com/sitemap.xml` in Search Console
   (and Bing Webmaster Tools — ChatGPT search leans on Bing's index).
9. **Google Business Profile**: confirm the website field still says
   `https://www.texashomeoutlet.com` (no other GBP action is needed for a
   same-domain change).
10. Run the Rich Results Test on `/` and one home detail URL; fix any
    structured-data errors same-day.

### First 30 days (monitoring)
- **Days 0–3**: URL-inspect the top 20 pages in Search Console; confirm
  "Page is indexed / canonical = user-declared". Watch server logs for
  Googlebot hitting legacy URLs and confirm 200/301 (never 404) responses.
- **Daily, weeks 1–2, then 3×/week**: Page Indexing report — the regression
  alarms are rises in "Soft 404", "Not found (404)", "Duplicate, Google chose
  different canonical", or any `noindex` exclusions on public pages.
- **Normal**: impressions wobble and a modest dip for a few weeks; Google
  says fluctuation during reprocessing is expected and same-domain moves
  settle in weeks, not months.
- **Action triggers**: clicks down >20–25% past week 3 with no recovery
  slope; 404s on URLs that have backlinks (fix same day); soft-404s rising.
- Keep all 301s for **at least 1 year** (Google's current guidance).
- Monthly: grep logs for `GPTBot|OAI-SearchBot|ClaudeBot|PerplexityBot` and
  spot-check "manufactured home dealers near Huffman TX" in ChatGPT,
  Perplexity, and Google AI Overviews to see what gets cited.

## Why it will be BETTER than before

The vendor site had no LocalBusiness/Product structured data, generic
metadata, and no AI-crawler-readable inventory. The new platform ships all
of it, plus a sitemap that updates with inventory and honest per-home pages.
The real AI-answer levers — complete GBP, LocalBusiness schema with hours,
crawlable non-JS inventory HTML, consistent NAP — are now all in place.

## Sources (research pass 2026-06-10)

- Google: [Site moves with URL changes](https://developers.google.com/search/docs/crawling-indexing/site-move-with-url-changes) · [Site moves without URL changes](https://developers.google.com/search/docs/crawling-indexing/site-move-no-url-changes) · [Redirects](https://developers.google.com/search/docs/crawling-indexing/301-redirects) · [JavaScript SEO basics](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics) · [Sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) · [robots.txt](https://developers.google.com/search/docs/crawling-indexing/robots/intro) · [Canonicalization](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls) · [LocalBusiness](https://developers.google.com/search/docs/appearance/structured-data/local-business) · [Product snippets](https://developers.google.com/search/docs/appearance/structured-data/product-snippet) · [Merchant listings](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing) · [Title links](https://developers.google.com/search/docs/appearance/title-link)
- "301s don't lose PageRank": [Search Engine Land (Illyes, 2016)](https://searchengineland.com/google-no-pagerank-dilution-using-301-302-30x-redirects-anymore-254608)
- JS rendering reality: [Vercel/MERJ study](https://vercel.com/blog/how-google-handles-javascript-throughout-the-indexing-process) · [searchVIU: 69% of AI crawlers don't execute JS](https://www.searchviu.com/en/ai-crawlers-javascript-rendering/)
- Vehicle schema deprecated June 2025: [Search Engine Land](https://searchengineland.com/google-drops-reporting-on-several-structured-data-types-461744)
- llms.txt not consumed: [John Mueller](https://bsky.app/profile/johnmu.com/post/3lrshm4gggs2v) · [SE Ranking 300k-domain study](https://seranking.com/blog/llms-txt/) · [noindex llms.txt guidance](https://www.searchenginejournal.com/google-says-it-could-make-sense-to-use-noindex-header-with-llms-txt/551744/)
- AI crawler docs: [OpenAI](https://developers.openai.com/api/docs/bots) · [Anthropic](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) · [Perplexity](https://docs.perplexity.ai/guides/bots)
