# DNS rollback — two-record revert

**Gate:** owner / Mark only. Do not change DNS without that approval.

This is the emergency revert for the 2026-06-14 website cutover. It puts apex
`A` and `www` `A` back on the pre-cutover host, and it removes the four Google
`A` records plus the `www` CNAME to `ghs.googlehosted.com`. **Never touch NS or
MX.** Companion history: [DNS_CUTOVER_RUNBOOK.md](../DNS_CUTOVER_RUNBOOK.md),
[CUTOVER_GUIDE.md](../CUTOVER_GUIDE.md).

TTL is 300 seconds, so a correct revert is visible in **≤ 5 minutes**.

## Pre-check (required)

Confirm the old host still answers **before** relying on this rollback. Last
verified June 2026.

```bash
curl -sI --max-time 10 http://52.10.0.211 | head -5
```

If the old host does not answer, this rollback takes the public hostname off
Cloud Run without restoring a working origin. Stop and get owner / Mark
approval for a different recovery path.

## Two-record revert

| Record | Action | After rollback |
|---|---|---|
| `texashomeoutlet.com` (apex) | Remove the four Google A records (`216.239.32.21`, `216.239.34.21`, `216.239.36.21`, `216.239.38.21`). Restore a single A to the pre-cutover host. | `52.10.0.211` |
| `www.texashomeoutlet.com` | Remove the CNAME to `ghs.googlehosted.com`. Restore A to the pre-cutover host. | `52.10.0.211` |

Leave NS and MX unchanged. Inbound Yahoo / Turbify mail depends on the apex MX
staying exactly as it is.

## Verify

Authoritative nameserver first, then a public resolver:

```bash
dig +short texashomeoutlet.com A @ns-26.awsdns-03.com.
dig +short texashomeoutlet.com A @8.8.8.8
dig +short MX texashomeoutlet.com
```

Expect the apex `A` to be the pre-cutover host, and MX to remain Yahoo.
