# Set Up Your Passkey for Texas Home Outlet

**For everyone on the team — no tech skills required. ~3 minutes.**

A passkey replaces typing the admin PIN. Once it's set up:
- On your laptop: tap your fingerprint sensor (or click "Sign in" → use Touch ID / Windows Hello).
- On your phone: tap Face ID or your fingerprint.
- That's it. No more typing PINs.

If you lose your laptop or phone, you can still use the PIN — passkeys are an *extra* convenience, not a replacement for the PIN you already know.

---

## Pick the section that matches your device

- [Mac (laptop or desktop)](#mac-laptop-or-desktop)
- [Windows PC](#windows-pc)
- [iPhone or iPad](#iphone-or-ipad)
- [Android phone](#android-phone)
- [Using Proton Pass (any device)](#using-proton-pass-any-device)
- [Adding a second passkey](#adding-a-second-passkey-recommended)
- [If something goes wrong](#if-something-goes-wrong)

---

## Mac (laptop or desktop)

**You'll need:** Touch ID set up on your Mac (most newer MacBooks and Magic Keyboards), OR your Mac password.

1. **Open the THO admin site.** Go to https://sapphirealpha.xyz on Safari, Chrome, or your usual browser.
2. **Click the lock icon** in the top-right corner of the page (it looks like 🔒).
3. **Type the admin PIN once.** This proves it's you. (You only do this on first setup; later you'll skip it.)
4. **Click "Settings"** in the menu that appears after you log in.
5. **Click "Set up a passkey"** — a small box from your Mac will pop up saying *"Sign in with Touch ID"* or *"Allow this website to save a passkey?"*
6. **Tap your fingerprint** on the Touch ID sensor (or type your Mac password if asked).
7. **Type a name for this passkey** so you remember which device it's on. Example: *"Mark's MacBook"*.
8. **Done.** You'll see a green checkmark.

**To sign in next time:**
1. Open https://sapphirealpha.xyz, click the 🔒 icon.
2. Click **"Sign in with passkey"**.
3. Tap your fingerprint when the box pops up.
4. You're in.

---

## Windows PC

**You'll need:** Windows Hello set up (fingerprint, face camera, or PIN).

1. **Open the THO admin site.** Go to https://sapphirealpha.xyz on Edge or Chrome.
2. **Click the lock icon** in the top-right (🔒).
3. **Type the admin PIN once** to log in.
4. **Click "Settings"** in the menu.
5. **Click "Set up a passkey"** — Windows will pop up a box that says *"Make sure it's you"* or *"Use Windows Hello"*.
6. **Tap your fingerprint, look at the camera, or type your Windows PIN** — whichever your PC is set up for.
7. **Type a name for this passkey** like *"Office Windows PC"*.
8. **Done.**

**To sign in next time:**
1. Open https://sapphirealpha.xyz, click 🔒.
2. Click **"Sign in with passkey"**.
3. Use Windows Hello (fingerprint / face / PIN) when the box pops up.
4. You're in.

---

## iPhone or iPad

**You'll need:** Face ID or Touch ID set up.

1. **Open Safari** (not another browser — Safari is required on iPhone for the smoothest experience).
2. **Go to** https://sapphirealpha.xyz.
3. **Tap the lock icon** (🔒) in the top-right.
4. **Type the admin PIN once** to log in.
5. **Tap "Settings"**.
6. **Tap "Set up a passkey"** — iOS will pop up *"Save a passkey for sapphirealpha.xyz?"*
7. **Tap "Continue"**, then look at your phone for Face ID (or touch the home button for Touch ID).
8. **Done.** Your passkey is now saved in your iCloud Keychain — it'll automatically appear on your other Apple devices that share the same Apple ID.

**To sign in next time:**
1. Open Safari to https://sapphirealpha.xyz, tap 🔒.
2. Tap **"Sign in with passkey"**.
3. Look at your phone (Face ID) or tap fingerprint (Touch ID).
4. You're in.

---

## Android phone

**You'll need:** Fingerprint, face unlock, or screen lock set up.

1. **Open Chrome** on your Android phone.
2. **Go to** https://sapphirealpha.xyz.
3. **Tap the lock icon** (🔒) in the top-right.
4. **Type the admin PIN once** to log in.
5. **Tap "Settings"**.
6. **Tap "Set up a passkey"** — Android pops up *"Use your screen lock to save a passkey?"*
7. **Use your fingerprint, face, or screen lock pattern**.
8. **Done.** Your passkey is now saved in your Google Account — it'll appear on your other Android devices signed into the same Google Account.

**To sign in next time:** open Chrome to https://sapphirealpha.xyz, tap 🔒, tap **"Sign in with passkey"**, use your fingerprint or face.

---

## Using Proton Pass (any device)

If you already have **Proton Pass** installed (browser extension on desktop, or app on phone), it'll handle the passkey for you:

1. Follow the steps above for your device.
2. When the popup appears asking where to save the passkey, you'll see Proton Pass listed alongside your device's built-in option (Touch ID, Windows Hello, etc.).
3. **Pick Proton Pass** if you want the passkey to sync across all your devices via your Proton account.
4. **Pick the device option** if you only want this passkey on this one device.

**Tip:** for the best balance, save TWO passkeys — one in Proton Pass (syncs everywhere) and one on your device (works even if Proton is down). See [Adding a second passkey](#adding-a-second-passkey-recommended).

---

## Adding a second passkey (recommended)

Always set up at least two passkeys on different devices. If you lose one, the other still works. Plus the PIN remains as a final fallback.

Repeat the setup steps on your second device. Each device gets its own passkey. The system shows them all in **Settings → Passkeys**, with the name you gave each one. You can revoke any passkey there if you lose a device.

**Suggested setup:**
- Passkey #1: Your work laptop (Mac or Windows)
- Passkey #2: Your phone (iPhone or Android)
- Passkey #3 (optional): A hardware key like a YubiKey kept in a safe place

---

## If something goes wrong

### "I don't see the lock icon (🔒)"
The page might still be loading, or your browser is too narrow. Refresh the page (⌘+R on Mac, Ctrl+R on Windows). On phones, the icon may be inside a hamburger menu (☰).

### "The passkey popup didn't appear"
Your browser or device may not support passkeys. Make sure you're on:
- Safari 16 or newer (Mac/iPhone/iPad)
- Chrome 109 or newer (any device)
- Edge 109 or newer (Windows)
- Firefox 122 or newer
- iOS 16+ or Android 9+

If your browser is older, **update it** from the App Store / Microsoft Store / Google Play.

### "I clicked something and now I'm locked out"
You're not. The PIN still works — click 🔒, type the PIN, you're back in. Then try the passkey setup again.

### "I lost my phone / laptop with my passkey"
Sign in on a different device using the PIN. Go to **Settings → Passkeys**, find the lost device's passkey, and click **Revoke**. That passkey can never be used again. Set up a new passkey on your replacement device.

### "I forgot the PIN AND lost all my passkeys"
Tell Ari. The PIN is rotated through Google Secret Manager — Ari can give you a new one in about 60 seconds.

### "It says 'this device can't use passkeys'"
Some old browser settings or restrictive admin policies block passkeys. Try a different browser (Chrome works on every platform). If you're on a work-managed device, the IT admin may need to allow WebAuthn.

### "The passkey works on my phone but not my laptop"
Each device has its own passkey unless you're using Proton Pass / iCloud Keychain / Google Password Manager to sync them. To sync, set up a passkey while signed into Proton Pass (or your platform's keychain) instead of saving it directly to the device.

---

## Quick reference card (print this)

| To do this | On Mac / Windows | On iPhone / Android |
|---|---|---|
| **Open admin site** | Browser → sapphirealpha.xyz | Safari/Chrome → sapphirealpha.xyz |
| **Open login** | Click 🔒 (top-right) | Tap 🔒 (top-right) |
| **Sign in (after setup)** | Click "Sign in with passkey" → fingerprint/face | Tap "Sign in with passkey" → Face ID/fingerprint |
| **Set up new passkey** | Settings → "Set up a passkey" → fingerprint | Settings → "Set up a passkey" → Face ID |
| **Remove a passkey** | Settings → Passkeys → Revoke | Settings → Passkeys → Revoke |
| **Forgot PIN?** | Ask Ari (~1 min) | Ask Ari (~1 min) |

---

## Why we're doing this

PINs work, but they have problems:
- Anyone watching you type can memorize it
- Phishing sites can trick you into typing it on a fake login page
- If the PIN leaks, *everyone* on the team has to learn a new one

Passkeys solve all three:
- Nothing to type — your fingerprint never leaves your device
- A passkey only works on the real `sapphirealpha.xyz` — fake sites can't use it
- If your passkey is compromised, only your one device is affected — everyone else is fine

The PIN stays as a fallback so no one ever gets locked out.

---

*Questions? Ping Ari. Last updated: 2026-05-02.*
