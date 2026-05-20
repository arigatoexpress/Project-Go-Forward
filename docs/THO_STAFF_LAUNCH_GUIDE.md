# Texas Home Outlet Staff Launch Guide

This guide is for Texas Home Outlet staff using the new Project Go Forward site and back-office tools. The business name used in documents is **Texas Home Outlet, Inc.**

## Staff Access

Open the production app at:

- `https://tho.sapphirealpha.xyz`
- Document Center: `https://tho.sapphirealpha.xyz/documents`
- CRM: `https://tho.sapphirealpha.xyz/crm`

Admin tools require an approved staff session. Use the owner-provided PIN or an enrolled passkey. Do not send the PIN in email, chat, screenshots, support tickets, or customer-facing documents.

If the app asks you to unlock again, enter the PIN once. A successful unlock should keep the admin session active while you work.

## Inventory And Floorplans

The public inventory is intentionally split into three groups:

| Group | Meaning | What customers should understand |
| --- | --- | --- |
| Available Now | Current lot homes available for sale | This is inventory THO can discuss as available now. |
| Pre-Owned | Used or resale homes | These may have different condition, pricing, and finance details. |
| Orderable | Manufacturer floorplans customers can custom order | These are catalog plans, not necessarily homes sitting on the lot. |

Current production baseline from the verified catalog API:

- 279 total homes and floorplans
- 19 current lot/pre-owned listings
- 260 orderable manufacturer floorplans
- 155 Matterport links in the merged catalog context

Do not mark an orderable floorplan as available unless it is truly on the lot. That distinction is important for customer trust and for the cutover review.

## Adding Or Changing Inventory

Use the staff/admin inventory workflow when it is available to you. If an editing screen does not expose a specific field yet, send the change to the internal operator instead of forcing it into the wrong field.

For each current lot home, collect:

- Manufacturer
- Model
- Year
- Serial number
- Price or "call for price" decision
- Status: available, pending, sold, or pre-owned
- Lot or inventory identifier if known
- Photos and floorplan image
- Matterport URL if available

For each orderable floorplan, collect:

- Manufacturer
- Series
- Model name
- Bedrooms, bathrooms, square feet, width, length, and sections
- Floorplan image
- Manufacturer-approved photos or media
- Source/provenance note

Use THO-owned lot photos or manufacturer-approved media. Do not use generic stock images for inventory cards.

## Photo Guidelines

For a current lot home, try to include:

- One clear exterior hero photo
- Kitchen photos
- Living area photos
- Bedroom photos
- Bathroom photos
- Floorplan drawing when available
- Matterport link when available

If photos are missing, the listing can still be entered, but flag it for follow-up before launch review.

## Document Center Workflow

Use Document Center to generate sales paperwork and closing packets.

1. Open `Documents`.
2. Start new, load an existing customer, or load an existing deal.
3. Complete buyer information.
4. Select or enter the home.
5. Fill every required field the app flags.
6. Select a packet or individual documents.
7. Generate and download the PDFs.

The app now fails closed for incomplete deals. If a customer/deal is only partially filled out, the system should not create a bad packet. It should show **Deal data needs attention** and list the missing fields.

## Required Before Generating Packets

At minimum, confirm:

- Buyer first and last name
- Buyer phone
- Manufacturer
- Model
- Serial number
- Installation street address
- Installation city
- Installation county
- Installation state
- Installation ZIP
- Sales price for sales/closing packets

Recommended before final customer packets:

- Buyer email
- HUD label
- Date of manufacture
- Wind zone
- Down payment
- Sales representative
- Lender/finance fields when the packet requires lender paperwork

If the app says a field is missing, add the real value before generating. Do not use fake placeholders like `TBD`, `UNKNOWN`, `123456`, or `SMOKE`.

## Manual Entry Fields

Some values are not always available from inventory feeds. The app may show helper text such as:

- `Enter manually - not in inventory feed`
- `Verify from data plate or order`
- `Enter if required by packet`

That is expected. Use the home order, data plate, customer record, or deal sheet to enter the value.

## When A Packet Will Not Generate

If the Generate button is disabled or the app shows **Deal data needs attention**:

1. Read the missing-field list.
2. Use the step buttons to go back to Customer Info or Home Selection.
3. Fill the exact missing field.
4. Return to Pick Documents.
5. Generate only when the app says the deal data is ready.

If generation fails after the app says the deal is ready, do not retry with made-up data. Save what you were doing, note the customer/deal name, and send the exact error text to Ari/internal support.

## Document Accuracy Rules

- The seller/legal entity should read **Texas Home Outlet, Inc.**
- Do not use the generated packet for a customer if required values are blank, fake, or guessed.
- Review generated PDFs before giving them to a customer.
- Use synthetic smoke-test records only for system testing. They should not be mixed with real customer work.

## Before Official Cutover

Staff can continue testing and preparing data in production, but do not announce the official domain switch until THO leadership approves the go-live window and the provider/DNS handoff items are complete.
