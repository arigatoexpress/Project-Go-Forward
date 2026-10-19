# Ad Studio click-path review

Reviewed from source at main `c73af0b`. This is a reading of the code, not a signed-in walkthrough.

**Signed-in click-through: UNKNOWN.** It needs a staff sign-in the owner provides. No Google Ads account calls were made. Nothing was deployed. No money was spent.

`/ad-studio` opens the Google Ads tab. `/studio` opens Create Ad, which is the home picker, the price box, Prepare Draft, and the flyer. Both addresses use the same sign-in gate.

## Step table

| Step | What staff see | Issue | Severity |
| --- | --- | --- | --- |
| 1. Open `/ad-studio` while signed out | The browser title says Google Ads. A panel titled **Staff sign-in** opens over a locked page. Steps: type your work email, open that email, tap **Sign me in**. Buttons: **Email me a sign-in link**, **Use backup PIN**, **Cancel**. Backup box label: **Backup PIN**. Button: **Unlock**. Helper: "Backup PIN from the owner. Most people should use the email button above." | **Addressed.** **Cancel** closes the panel onto a locked page. It says this page stays locked until you sign in. Buttons: **Staff sign-in** and **Back to home**. Staff do not land on the public chat. | Fixed |
| 2. Signed-in open | On refresh, the page says **Checking your sign-in...** until the session check returns. Then the code shows Ad Studio. The locked page is not shown during that wait. | Signed-in click-through is UNKNOWN. The locked-page flash on refresh is addressed in code. | UNKNOWN |
| 3. Page load | First line: **Loading...** Then the Google Ads card says **Loading Google Ads…** If that load fails: "Google Ads is unavailable right now. Nothing was sent to Google." Button: **Load again**. | None. The failure tells them to try again and that nothing was sent. | Low |
| 4. Google Ads, create | Heading **1. Create**. Label: **Who should see this ad?** Helper: the note stays on this screen and is not the ad text. Example button: **Use the example**. Main button: **Check this ad** (or **Use this description** when a check is not available). While it works: **Checking the campaign…** Helper under the button: "Checking confirms the ad on this website. Your description stays here. Nothing is sent to Google, and this does not spend money." Empty note: "Describe who should see this ad and what you want them to do." Short note: "Add a little more detail. One or two sentences is enough." | The word "campaign" is still on the checking line. The next step is on the same screen. | Low |
| 5. Google Ads, preview | Heading **2. Preview**. Name on the card: **Huffman search ads**, "still turned off." Location line uses 10685 FM 1960 East, Huffman, TX, within 50 miles. Budget line is on the card. Checkbox: "I read both ads, the Huffman area, and the budget. Launching leaves the ads off and does not spend money." | Budget amounts are waiting on the owner. See below. The address matches `config.yaml`. | Owner-pending |
| 6. Google Ads, launch | Heading **3. Launch**. Button: **Launch turned off**. While it runs: **Asking the owner to confirm…** Confirm box says the campaign is created turned off and does not spend money. If the person is not the owner: "Only the owner can launch this campaign. Sign in with the owner passkey, then try again." | **Launch** can sound like the ad will run. The rest of the sentence says turned off and no spend. Left as written. | Low |
| 7. Owner notes on that screen | A closed section titled **Technical details for the owner**. First line: "You can skip this. These notes are for the owner." Inside: Campaign name and Creation mode. | **Addressed.** Campaign id, content hash, ledger version, recorded events, and evidence lines are not shown to staff. Those values stay on the campaign record. The dispatch flag stays, in plain words. | Fixed |
| 8. Create Ad | Sidebar brand: **Tex's Ad Studio**. Subtitle: "Nothing is posted from here." Tab: **Create Ad**. Intro: "Write a social post for TikTok, Instagram, or Facebook, then review it. Nothing is posted from this screen. Public prices stay Call for Price unless the owner approved a number." Empty phone: "Your script will appear here." Helper: "Choose your settings and hit Generate." | `/ad-studio` does not land here. Staff use **Create Ad** or open `/studio`. | Low |
| 9. Choose a home | Button: **Choose a home**. While it loads: **Loading homes...** Or the line "or type a home here:" Panel title: **Choose a home**. Search box: "Search by name, maker, or bedrooms." Empty list: "No homes loaded yet." No match: "No homes match that search." A failed load says "Homes did not load. Please try again." with **Retry**. Each home still shows its on-screen price. | That price can be a listed amount even though the price box below stays blank. Hiding it is waiting on the owner: how staff enter prices. | Owner-pending |
| 10. Price box | The box starts empty. It is not filled from the home. Label for screen readers: **Price**. Placeholder: "Call for Price, unless the owner approved a number." Helper that stays visible: "The price box starts empty. Leave it blank unless the owner approved a number." | Typing in the box drops the selected-home card. The flyer does not print a number staff type unless the owner already approved that home. Both are waiting on the owner: how staff enter prices. | Owner-pending |
| 11. Write the script | Button: **Generate Script**. While it works: **Generating...** (or **Generating 2 Variations...**). Failure on the phone: "The script was not written. Nothing was posted. Please try again." Helper: "Nothing was posted. Press Retry to try the same step again." Button: **Retry**. | None after the copy fix. The words stay off the preview until a script exists. | Low |
| 12. Full preview | Button: **Full Preview & Images**. Title: **Ad Content Preview**. Close control is an X named **Close preview**. Badge: **Preview only**. Camera-note helper: "Words in [brackets] are camera notes. Do not read them out loud." | None. | Low |
| 13. Flyer | Heading: **Create Finished Flyer**. Button: **Make a flyer to download**. While it works: **Making the flyer...** No photo: "No photo selected. The flyer will use the store colors instead." No script and no home: "Write a script or pick a home first. Nothing was posted." Other failure: "The flyer was not created. Nothing was posted. Try again." Buttons: **Retry** and a small X. Success: **Finished Flyers**, then **Download flyer**. | The flyer price and the flyer phone are waiting on the owner. See below. A flyer with no script uses the business city from config. | Owner-pending |
| 14. Prepare Draft | Helper: "This adds a reviewed [TikTok, Instagram Reels, or Facebook] draft to the list on this screen. Nothing is sent to a social platform." Button: **Prepare Draft**. While it works: **Preparing...** The button is disabled until the save finishes. | None. It does not post. | Low |
| 15. Prepare Draft failure | The preview stays open. Message: **The draft wasn't saved. Please try again.** Offline: "You appear to be offline. Check your connection and try again." No connection: "We couldn’t reach the server. Check your connection and try again." The same **Prepare Draft** button is the retry. A failed save stays on the preview and no longer looks successful. | None. The old failure that closed the preview is already fixed in this source. | Low |
| 16. Draft success | The preview closes and the **Drafts** tab opens. Card title example: **TikTok draft**. Kind: **Video**. Status: **Ready to review here**. The card shows the script, shortened when it is long, and a **Copy** button. Confirmation: **Copied**. Notice: "Saved on this browser until you sign out or your session times out. Nothing was posted." Empty tab, before any draft: "No drafts prepared yet." Helper: "Write a script, open the full preview, then choose Prepare Draft. Nothing is posted." | **Addressed.** Drafts stay in this browser for the signed-in staff session. Sign-out clears them. A session timeout clears them too. They are not kept when signed out. | Fixed |

## Other errors on the same page

These are the messages after the copy fixes. Each one tells staff to try again, and the picture, spoken, slideshow, and video lines say nothing was posted.

| What failed | What staff see | Next step |
| --- | --- | --- |
| Content ideas | "Content ideas did not load. Please try again." Empty list: "No ideas came back." | **Refresh**, or go to Create Ad |
| Numbers page | "Performance numbers did not load. Please try again." Unreadable payload: "We could not read the numbers. Refresh and try again." | **Refresh** |
| Picture | "Describe the picture first. Nothing was posted." or "The picture was not created. Nothing was posted. Try again." The screen also asks "This creates a picture with AI. It can use a paid AI tool. It does not post the ad. Continue?" | **Retry** or change the description |
| Spoken version | "Spoken voices are not available right now. You can still copy the script and record it yourself." Empty list: "No spoken voices are set up yet. You can still copy the script and record it yourself." Too short: "The script is too short to read aloud. Add a little more, then try again." Other failure: "The spoken version was not created. Nothing was posted. Try again." | **Retry**, or copy the script |
| Slideshow | "Make the spoken audio and pick a home with photos first." or "The slideshow was not created. Nothing was posted. Try again." | **Retry** |
| AI video | "AI pictures and video are not available yet. You can still write a script and make a slideshow from the home’s photos. Ask the owner if you need an AI picture or video." Other failure: "The AI video was not created. Nothing was posted. Try again." Still working: "The AI video is still being made. Wait a minute and try again. Nothing was posted." | **Retry**, or make a slideshow |
| Homes list | "Homes did not load. Please try again." | **Retry** |

Raw tool names, stack traces, HTTP codes, and JSON bodies are no longer the text of these messages. The real error stays in the server log.

## Owner-pending

These were not decided here.

- **Which phone number the flyer shows.** Waiting on the owner. The flyer footer calls `business_phone()` in `config_loader`, which reads `config.yaml`. That phone is (281) 324-3020. A note in the flyer code still says the owner should confirm it, because an older footer did not match. The number was not changed.
- **How staff enter prices.** Waiting on the owner. The price box is blank on purpose. The placeholder still says Call for Price. The home list can still show a listed price from the public home list. The flyer ignores a typed price and prints "Call for price" unless that home is already marked owner-approved. Nothing here hides the list price or turns the typed box into the flyer price.
- **Ad budget.** Budget amounts are waiting on the owner. The Google Ads preview shows those amounts, and the screen says this launch leaves the ads off. The budget was not changed, and this screen cannot change it.

## What matches config.yaml

- Showroom address on the Google Ads preview is 10685 FM 1960 East, Huffman, TX. That is the address in `config.yaml`.
- Store hours are not typed onto this screen.
- The flyer phone comes from `config.yaml` through `business_phone()`. Which number the flyer shows is waiting on the owner.
- A flyer with no script uses the business city from config for the body line.

## Fixes made

Copy only. No workflow, config, or secret changes.

- Sign-in backup box says **Backup PIN** instead of admin PIN. The old standalone studio page says the same, and its failures are plain sentences.
- Home button says **Choose a home**. The empty list says "No homes loaded yet." The search box no longer says "model" or "status."
- The price box has a helper that it starts empty.
- Sidebar says nothing is posted. The preview badge says **Preview only**.
- Voice groups say Recommended, Broadcast, News style, and More voices. The list no longer shows voice codes such as Neural2.
- Flyer, script, picture, spoken version, slideshow, and AI video failures use plain sentences. They no longer include the raw error, "GenAI," "moviepy," "script_text," or "prompt."
- Facebook idea line no longer says "boost top performers."
- Google Ads setup line says "Google setup" instead of "Google Cloud." The owner-details section says staff can skip it.
- A script failure no longer tells staff to check the connection when the failure was something else.
- Flyer fallback city comes from the business city in config.

## Follow-up fixes

Addressed in #429, stacked on #428. No backend storage, API, or auth change.

- **Cancel** on signed-out `/ad-studio` closes the sign-in panel onto a locked page. It says the page stays locked until you sign in. **Staff sign-in** opens the panel again. **Back to home** leaves the page.
- A prepared draft stays in this browser until sign-out or the session times out. The saved list is keyed to the staff session cookie already on this device. Sign-out clears it. A session timeout clears it. A signed-out visit does not keep it. A new sign-in does not keep the previous session's drafts.
- The draft card shows the script. Long text is shortened on the card. **Copy** uses the clipboard and then says **Copied**.
- Campaign id, content hash, ledger version, recorded events, and evidence lines are not shown on the staff screen. Campaign name, creation mode, and the dispatch flag stay in the closed owner notes. The hidden values remain on the campaign record.

## Session check on refresh

Refreshing `/ad-studio` waits on `/api/admin/check` before it chooses a page. This is a frontend wait. The session check itself is unchanged.

- While that check is still running, the page says **Checking your sign-in...**
- A signed-in session then opens Ad Studio.
- A signed-out session then shows the locked page.
- The locked page is not shown during the wait.

## Planned follow-ups

None from the list above. Those four are addressed in #429, stacked on #428.

## Left

- Signed-in click-through: UNKNOWN.
- The word **Launch** remains on **Launch turned off**.
- Which phone the flyer shows, how staff enter prices, and the ad budget, as listed above. Budget amounts are waiting on the owner.
- Typing a price still clears the selected-home card. That is behavior, not a wording change, so it was left.
- The writing guide inside the tool still mentions Houston. Staff do not see that guide.
- Some buttons still say **Generate Script** and **Generate Image**. The guide already uses Generate Script.
- `docs/team/staff-gotchas.md` tells staff not to press the Google Ads buttons because those ads cost money when they run. The screen says this page cannot turn ads on and does not spend money. The gotcha was not rewritten here.
