# Staff sign-in and passkey setup

Sign in with email. The short instructions are in
[staff-sign-in.md](staff-sign-in.md). Bookmark
**https://www.texashomeoutlet.com/staff**.

Passkeys are optional and are not on the staff sign-in page. A passkey saved
on the old temporary site will not work on www.texashomeoutlet.com. You do not
need one to get into the admin tools. Daily sign-in is the email link in
[staff-sign-in.md](staff-sign-in.md).

The owner keeps **Sign in with Passkey** off. It appears only as a small link
under **Use backup PIN** when `STAFF_PASSKEY_SIGN_IN` is turned on
(`feature_flags` in `config.yaml`, or env `FF_STAFF_PASSKEY_SIGN_IN=1`).
That switch does not turn off the passkey APIs used after someone is already
signed in.

If none works, contact your THO site administrator through the established
support channel. Do not send your PIN, email code, recovery codes, or passkey
secrets in a support message. Access or recovery cannot be guaranteed until
an appropriate method is tested.

## Register a staff passkey

1. Sign in on **www.texashomeoutlet.com** using a working method above.
2. Click the key button labeled **Register this device passkey** in the top
   navigation, or **Register passkey** in the mobile menu.
3. Enter your approved **@texashomeoutlet.com** staff address.
4. Click **Register passkey** and follow your browser or password manager's
   prompt. Use the offered fingerprint, face, screen-lock PIN, or password.
5. Wait for **Passkey registered for this staff email.**
6. Keep a recovery method available. Sign out, reopen the canonical site, and
   sign back in with the email link. Saving a key alone is not a verified
   sign-in. Do not look for **Sign in with Passkey** on `/staff` unless the
   owner setting above is on.

Owner credentials have additional protections. Registering or revoking an owner
credential requires the same owner's existing passkey session; a shared PIN or
email-code session cannot bypass that restriction.

## If you already registered

Sign in with the email link. Do not repeat enrollment merely because somebody's
checklist has not been updated. If the owner has turned the passkey link on
and that key works on the canonical site, report that successful sign-in and
whether your recovery method also works.

A key saved for the old temporary site may not appear on the canonical site.
Sign in on the canonical site with the email link or backup PIN. Register a
canonical-site key only if an owner still needs one for a tool that asks for
it. **Keep working legacy credentials and legacy-domain support until
canonical sign-in and recovery are confirmed.**

If the provider says a THO key already exists, try using it or choose another
provider/device. Do not delete a working key just to clear a registration error.

## Lost device or unusable key

1. Sign in using another working method.
2. Open **System Hub → Passkey Recovery**.
3. Identify the exact lost or unusable credential before selecting **Revoke**.
   If you cannot identify it confidently, ask the site administrator for help.
4. Register and test a replacement. Preserve another working sign-in method.

For additional resilience, register a second trusted device or provider and
test it. Sync behavior depends on your selected device/password manager; do not
assume a key is available everywhere until you have tested it.

## What to confirm to the site administrator

- You signed in successfully at **www.texashomeoutlet.com**.
- Which method worked: passkey, PIN, or email code. Do not include the secret.
- Whether a second sign-in/recovery method worked.
- Any failing step and the visible error, with secrets removed.

These confirmations establish staff access only. They do not approve inventory
accuracy, outbound email delivery, or the overall client handoff. See
[client acceptance](../CLIENT_HANDOFF_ACCEPTANCE.md).

Updated October 6, 2026. Email is the staff sign-in. Passkey sign-in stays off unless the owner flag is on.
