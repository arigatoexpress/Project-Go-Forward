# Store hours and the search listing

This note is for the Texas Home Outlet team. Ask the owner when the hours need to change. You do not edit the website files yourself.

## Where the hours live

The store hours are written in `config.yaml`. That file is the schedule to trust.

Today those lines say:

- Mon-Fri 9:00 AM - 6:00 PM
- Sat 10:00 AM - 3:00 PM
- Sun Closed

Saturday is 10:00 AM to 3:00 PM. Sunday is closed.

## Change these four files together

An hours change has to update four files in the same change. A test fails if any one of them is left behind.

1. `config.yaml`. This is the schedule.
2. `frontend/src/constants.js`. The line is named `BUSINESS_HOURS`. Visitors read this line on the website. Keep it in the short form already in that file. Right now it says `Mon-Fri 9-6, Sat 10-3, Sun Closed`.
3. `docs/content/FAQ.md`. The hours on that page must match the lines in `config.yaml`.
4. `tests/test_public_hours.py`. This file writes the hours down again, including the short website line and the Saturday hours, and its FAQ test checks that `docs/content/FAQ.md` matches `config.yaml`. Update those fixed hours in the same change.

## Who to ask

Ask the owner. Tell them the new weekday hours, Saturday hours, and Sunday hours in plain words. They update all four files in one change.

## After hours or page wording change

The sitemap has a last updated date for pages (after #423 merges). Search engines use that date to notice a page changed. After the hours change, or after the words on a page change, the owner bumps that date by hand for the pages that changed (after #423 merges). Ask for this in the same request.

## Steps

1. Write down the new hours for weekdays, Saturday, and Sunday.
2. Ask the owner to update these four files together: `config.yaml`, the `BUSINESS_HOURS` line in `frontend/src/constants.js`, `docs/content/FAQ.md`, and `tests/test_public_hours.py` (its fixed hours and the FAQ test pin them).
3. Ask the owner to bump the sitemap last updated date for every page whose hours or wording changed (after #423 merges).
4. Wait until the owner says the update is on the website.

## How to check it worked

Open https://www.texashomeoutlet.com on a computer. The colored strip across the top shows the hours. On a phone, that strip hides the hours, so read them on the Contact page or the FAQ page instead. They should match the hours you asked for. After #423 merges, open https://www.texashomeoutlet.com/sitemap.xml and find the pages that changed. The last updated date next to those pages should be the day of the change.
