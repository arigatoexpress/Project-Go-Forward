# Store hours and the search listing

This note is for the Texas Home Outlet team. Ask the owner when the hours need to change. You do not edit the website files yourself.

## Where the hours live

The store hours are written in `config.yaml`. That file is the schedule to trust.

Today those lines say:

- Mon-Fri 9:00 AM - 6:00 PM
- Sat 10:00 AM - 3:00 PM
- Sun Closed

Saturday is 10:00 AM to 3:00 PM. Sunday is closed.

## The website has a second copy

Visitors read the hours from a second line in `frontend/src/constants.js`. The line is named `BUSINESS_HOURS`. Right now it says:

`Mon-Fri 9-6, Sat 10-3, Sun Closed`

Change that line at the same time as `config.yaml`. Keep the website line in the short form already in that file. A test fails if the two copies do not match.

## Who to ask

Ask the owner. Tell them the new weekday hours, Saturday hours, and Sunday hours in plain words. They update both copies in one change.

## After hours or page wording change

The sitemap has a last updated date for pages. Search engines use that date to notice a page changed. After the hours change, or after the words on a page change, the owner bumps that date by hand for the pages that changed. Ask for this in the same request.

## Steps

1. Write down the new hours for weekdays, Saturday, and Sunday.
2. Ask the owner to update `config.yaml` and the `BUSINESS_HOURS` line in `frontend/src/constants.js` together.
3. Ask the owner to bump the sitemap last updated date for every page whose hours or wording changed.
4. Wait until the owner says the update is on the website.

## How to check it worked

Open https://www.texashomeoutlet.com and read the hours in the top bar, on Contact, or on the FAQ page. They should match the hours you asked for. Then open https://www.texashomeoutlet.com/sitemap.xml and find the pages that changed. The last updated date next to those pages should be the day of the change.
