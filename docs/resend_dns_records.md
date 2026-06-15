# Resend DNS Records — Texas Home Outlet

**Domain:** `texashomeoutlet.com`  
**Resend domain ID:** `cab685a4-93c6-4d3d-a4e5-a8db742ad1ff`  
**Resend region:** `us-east-1`  
**Status:** `not_started` until the records below propagate and Resend verifies them.

> These records are required for Resend to send email from `noreply@texashomeoutlet.com`.
> **Apex Yahoo inbound MX is NOT touched** — every record lives on a subdomain (`send`,
> `resend._domainkey`) or on `_dmarc`. Inbound mail continues to flow to Yahoo Business.

---

## Records to add in Turbify Domain Control Panel

| # | Type | Host / Name | Value | Priority | TTL |
|---|------|-------------|-------|----------|-----|
| 1 | TXT | `resend._domainkey` | `p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCmCVP3/RwIWdZTaGobt9obpatRFZxYIK8KyaA9yrlVdowSaKb88+7GaYS6iAe/rNsLy0EhWa032lB1JNE9qkw7I2p16AAtuV8uBJgidWJ338VcoECt0R9sacTRbbkGiE91lDMRt+qm91mxCO71z0uMTXLCmOr6MXqoZdrSRhW6+QIDAQAB` | — | Auto/3600 |
| 2 | MX | `send` | `feedback-smtp.us-east-1.amazonses.com` | `10` | Auto/3600 |
| 3 | TXT | `send` | `v=spf1 include:amazonses.com ~all` | — | Auto/3600 |
| 4 | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:aristotlespec@gmail.com` | — | Auto/3600 |

### Notes

- **#1 (DKIM)** value is ~216 characters — under the 255-character single-string TXT limit.
  Paste it as **one unbroken string** (no quotes, no line breaks).
- **#2 / #3** are the `send` subdomain's bounce-MX and SPF (Amazon SES under the hood).
  They do **not** replace the apex Yahoo MX because they live on a different host.
- **#4 (DMARC)** is optional but recommended. It starts in monitor-only mode (`p=none`)
  and reports to `aristotlespec@gmail.com`. Skip this row if Turbify already has a
  `_dmarc` record you want to keep.
- If Turbify auto-appends the domain to the Host field, enter just the left label
  (e.g. `resend._domainkey`, `send`, `_dmarc`). If it wants the FQDN, use
  `resend._domainkey.texashomeoutlet.com`, `send.texashomeoutlet.com`, etc.

---

## Verification

After the records are added, verify the domain via the Resend API:

```bash
curl -X POST https://api.resend.com/domains/cab685a4-93c6-4d3d-a4e5-a8db742ad1ff/verify \
  -H "Authorization: Bearer $RESEND_ADMIN_KEY"
```

Or click **Verify** in the Resend dashboard. Once status flips to `verified`, the
next Cloud Run deploy automatically uses the validated sending key (`resend-api-key`
version 3 / latest).

---

## DNS health checks from the THO app

The app exposes `/api/v1/cutover/mx-status` (partner API key required) which reports:

- Inbound Yahoo MX preserved on the apex.
- Resend outbound MX present on `send.texashomeoutlet.com`.
- SPF, DKIM, and DMARC TXT records (when `dns_mx_cutover.py` is updated to check them).

This endpoint is read-only and safe to poll continuously.
