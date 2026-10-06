# Turn on email sign-in

Do these steps in order. Do not merge the staff sign-in change until the secret in step 3 exists. Merging builds a no-traffic candidate. It does not put the new page on the public site. Do not move traffic, change DNS for the website, or change who can access the project.

The message the team should bookmark is https://www.texashomeoutlet.com/staff

## 1. Create the Resend API key

1. Open https://resend.com and sign in.
2. Click **API Keys**.
3. Click **Create API Key**.
4. Name it `staff sign-in`.
5. Choose **Sending access**. Do not choose full access.
6. Click **Add**.
7. Copy the key. Resend shows it once. Keep it in a password manager. Do not email it, do not put it in GitHub, and do not paste it into chat.

## 2. Add the domain in Resend and copy its DNS records

1. In Resend, click **Domains**.
2. Click **Add Domain**.
3. Enter `texashomeoutlet.com`.
4. Leave receiving turned off. This is only for mail the office sends.
5. If it asks for a region, choose the United States.
6. Open the domain's **Records** tab.

Resend shows a table. Each row has a **Type**, a **Name**, and a **Value**. Copy those three cells exactly. This page does not list the values, because Resend creates them for your domain.

The Type column is one of these:

- **TXT** for DKIM, and often for SPF.
- **MX** for the sending return path, on older domains. That row belongs on the name Resend shows (often a `send` name). It is not the company's main incoming mail.
- **CNAME** instead of the TXT plus MX pair, for domains added after August 2026.

Add every row Resend shows. Do not invent a value, and do not skip a row.

7. Open the place that already holds the DNS records for texashomeoutlet.com. That may be the domain registrar. It is not always Google Cloud.
8. Add each row. If a row is a CNAME, turn off any proxy (such as Cloudflare's orange cloud) on that row.
9. Do not delete or replace the records that deliver the company's normal email.
10. Back in Resend, click **Verify**. Wait until the domain says verified. DNS can take a while. Click verify again later if it is still pending.

The app sends from `Texas Home Outlet <noreply@texashomeoutlet.com>` unless `RESEND_FROM` is set to another address on this same domain.

## 3. Put the key in Secret Manager

1. Open https://console.cloud.google.com
2. At the top, select project **tho-ai-agent**.
3. In the search bar, type **Secret Manager** and open it.
4. Click **Create secret**.
5. Name: `resend-api-key`
6. Secret value: paste the Resend key from step 1.
7. Leave the other fields as they are. Click **Create**.

## 4. Let Cloud Run read that secret

The deploy workflow does not set a custom service account. This repo records the current Cloud Run runtime account as:

`691674245427-compute@developer.gserviceaccount.com`

Confirm that before you grant anything:

1. In the same project, search **Cloud Run** and open it.
2. Open the service **project-go-forward** in region **us-central1**.
3. Open the **Security** tab, or click **Edit and deploy new revision** and look at the service account. Do not deploy a new revision.
4. The account shown there is the one that needs access. It should be the address above. If the screen shows a different address, use the address on the screen.

Then grant access on this secret only:

1. Go back to **Secret Manager**.
2. Open the secret **resend-api-key**.
3. Open the **Permissions** tab. Do this on the secret, not on the project's main IAM page.
4. Click **Grant access**.
5. New principals: paste the service account from the Cloud Run screen.
6. Role: **Secret Manager Secret Accessor**.
7. Click **Save**.

Do not give that account Secret Manager Admin, and do not grant this role on the whole project.

## 5. Test the candidate before the public site changes

After the sign-in change is merged, GitHub builds a candidate and does not send customers or staff to it.

1. Open the GitHub Actions run named **Deploy to Cloud Run** for that merge.
2. In the deploy log, copy the URL that starts with `https://candidate---`. You can also find it in Cloud Run under **Revisions**, on the revision whose tag is `candidate`.
3. Do not open **Manage traffic**. Do not move any slider.
4. On the end of that candidate URL, add `/staff` and open it. Example shape: `https://candidate---....run.app/staff`
5. You should see one big email box and **Email me a sign-in link**.
6. Type your own work email and tap the button.
7. Stay on the candidate page. Open the email. The **Sign me in** button in the email opens www.texashomeoutlet.com, which is still the old site until traffic is moved on purpose. Do not use that button for this test.
8. On the candidate page, tap **Type the 6-digit code instead**. Type the code from the email. Tap **Verify**.
9. You should see **You are signed in**. Tap **Sign out** when you are done.

Moving this version onto www.texashomeoutlet.com is a separate traffic change. It is not part of these steps.

## Message to send the staff

Copy everything between the lines:

---

Hi team. Signing in is just your work email.

Open https://www.texashomeoutlet.com/staff and bookmark it. Type your work email and tap Email me a sign-in link. Open the message and tap Sign me in. You stay signed in on that phone or computer. If no email arrives, check spam, then tell me so I can add you.

---
