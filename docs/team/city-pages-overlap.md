# City page overlap

Measured 2026-10-20 with the 5-word shingle method in the packet's `overlap.py`.

Scripts, styles, and noscript blocks are removed. Tags are stripped. Text is unescaped and lowercased. Tokens are `[a-z0-9$']+`. A shingle is five tokens in a row. Jaccard is `|A intersect B| / |A union B|`. Contained is `|A intersect inventory| / |A|`.

The production pages in the packet were about 98 percent the same because each town page repeated the full home catalog and swapped the city name. These renders cap that list at 8 homes. The full catalog stays on `/inventory`. Each town page adds its own local section from `frontend/src/content/cityLocalFacts.json`. `/locations` links every town page. Breadcrumbs are Home, Locations, then Homes in {City}, TX.

## Packet baseline (live HTML, before this change)

Source: packet `overlap.txt`, fetched from the live site.

| Page | Words | Jaccard vs Baytown | Jaccard vs inventory | Contained in inventory |
|---|---:|---:|---:|---:|
| atascocita | 1178 | 98.1% | 5.7% | 14.5% |
| baytown | 1178 | 100.0% | 5.7% | 14.5% |
| beaumont | 1178 | 98.1% | 5.7% | 14.5% |
| cleveland | 1178 | 98.1% | 5.7% | 14.5% |
| conroe | 1178 | 98.1% | 5.7% | 14.5% |
| crosby | 1178 | 98.1% | 5.7% | 14.5% |
| huffman | 1178 | 98.1% | 5.8% | 14.7% |
| humble | 1178 | 98.1% | 5.7% | 14.5% |
| kingwood | 1178 | 98.1% | 5.7% | 14.5% |
| livingston | 1178 | 98.1% | 5.7% | 14.5% |
| new-caney | 1181 | 98.0% | 5.7% | 14.5% |

Inventory words: 2994.

## Controlled before and after (same rendered catalog)

This is the comparison that uses one catalog before the copy change and after it. The catalog is 280 orderable homes, padded so the before pages land near the packet's word count (about 1,178 live words, 1,252 rendered words). Both sides were rendered by `seo_routes.render_spa_response` and scored with `overlap.py`. Inventory words: 3,224 before, 3,225 after.

| Page | Before words | Before vs Baytown | After words | After vs Baytown | Before vs inventory | After vs inventory | Before contained | After contained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| atascocita | 1252 | 98.2% | 248 | 29.2% | 1.6% | 2.5% | 3.6% | 18.1% |
| baytown | 1252 | 100.0% | 258 | 100.0% | 1.6% | 2.5% | 3.6% | 17.6% |
| beaumont | 1252 | 98.2% | 243 | 29.6% | 1.6% | 2.5% | 3.6% | 18.5% |
| cleveland | 1252 | 98.2% | 219 | 31.8% | 1.6% | 2.5% | 3.6% | 20.7% |
| conroe | 1252 | 98.2% | 233 | 30.4% | 1.6% | 2.5% | 3.6% | 19.3% |
| crosby | 1252 | 98.2% | 231 | 30.7% | 1.6% | 2.5% | 3.6% | 19.5% |
| huffman | 1252 | 98.2% | 231 | 30.7% | 1.7% | 2.7% | 3.8% | 20.9% |
| humble | 1252 | 98.2% | 240 | 29.9% | 1.6% | 2.5% | 3.6% | 18.7% |
| kingwood | 1252 | 98.2% | 258 | 28.7% | 1.6% | 2.5% | 3.6% | 17.6% |
| livingston | 1252 | 98.2% | 252 | 28.9% | 1.6% | 2.5% | 3.6% | 17.8% |
| new-caney | 1255 | 98.2% | 251 | 29.2% | 1.6% | 2.5% | 3.6% | 18.1% |

The contained share rises because the page is shorter. The shared piece is a short list of real homes, not a copied catalog. Jaccard versus Baytown falls from 98.2 percent to about 29 to 32 percent.

## Repo inventory snapshot (19 homes)

Same method on `data/legacy_site/legacy_inventory_context.json`. Inventory words: 567 before, 568 after.

| Page | Before words | Before vs Baytown | After words | After vs Baytown | Before vs inventory | After vs inventory | Before contained | After contained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| atascocita | 326 | 90.6% | 293 | 36.8% | 14.9% | 10.7% | 30.0% | 19.8% |
| baytown | 326 | 100.0% | 303 | 100.0% | 14.9% | 10.6% | 30.0% | 19.4% |
| beaumont | 326 | 90.6% | 288 | 37.3% | 14.9% | 10.8% | 30.0% | 20.1% |
| cleveland | 326 | 90.6% | 264 | 39.7% | 14.9% | 11.4% | 30.0% | 22.1% |
| conroe | 326 | 90.6% | 278 | 38.2% | 14.9% | 11.0% | 30.0% | 20.9% |
| crosby | 326 | 90.6% | 276 | 38.5% | 14.9% | 11.1% | 30.0% | 21.1% |
| huffman | 326 | 90.6% | 276 | 38.5% | 15.4% | 11.8% | 30.9% | 22.3% |
| humble | 326 | 90.6% | 285 | 37.6% | 14.9% | 10.9% | 30.0% | 20.4% |
| kingwood | 326 | 90.6% | 303 | 36.2% | 14.9% | 10.6% | 30.0% | 19.3% |
| livingston | 326 | 90.6% | 297 | 36.5% | 14.9% | 10.6% | 30.0% | 19.5% |
| new-caney | 329 | 90.2% | 296 | 36.8% | 14.8% | 10.7% | 29.9% | 19.8% |

## What each page is allowed to say

County, highways, and place facts come from the Handbook of Texas page named below. Pages were read on 2026-10-20. Compass direction is a 16-point bearing from the showroom point in `config.yaml` `business.geo` (latitude 30.018056, longitude -95.115729) to the latitude and longitude printed on that handbook page. The config comment says those showroom coordinates are a US Census geocoder match for the showroom street address. The street address is `business.address`: 10685 FM 1960 East, Huffman, TX 77336. No drive times are stated. No miles were estimated.

Humble is rounded to 'west' at the WSW/W boundary (bearing 258.70 deg; WSW ends at 258.75).

| Town | Handbook page | Handbook coordinates | Bearing from the showroom |
|---|---|---|---|
| Huffman | https://www.tshaonline.org/handbook/entries/huffman-tx | 30.02716260, -95.08576270 | Showroom town. The page does not treat Huffman as a direction away from the lot. |
| Humble | https://www.tshaonline.org/handbook/entries/humble-tx-harris-county | 29.99199250, -95.26579700 | west |
| Atascocita | https://www.tshaonline.org/handbook/entries/atascocita-tx | 29.99883060, -95.17659780 | west-southwest |
| Crosby | https://www.tshaonline.org/handbook/entries/crosby-tx | 29.91188850, -95.06215120 | south-southeast |
| Kingwood | https://www.tshaonline.org/handbook/entries/kingwood-tx | 30.03355260, -95.26104440 | west |
| New Caney | https://www.tshaonline.org/handbook/entries/new-caney-tx | 30.15521600, -95.21132210 | north-northwest |
| Baytown | https://www.tshaonline.org/handbook/entries/baytown-tx | 29.75364790, -94.97003800 | south-southeast |
| Beaumont | https://www.tshaonline.org/handbook/entries/beaumont-tx | 30.08439430, -94.14408200 | east |
| Cleveland | https://www.tshaonline.org/handbook/entries/cleveland-tx-liberty-county | 30.34381200, -95.08121100 | north |
| Conroe | https://www.tshaonline.org/handbook/entries/conroe-tx | 30.32052800, -95.46764000 | northwest |
| Livingston | https://www.tshaonline.org/handbook/entries/livingston-tx | 30.70973880, -94.93764700 | north-northeast |

Kingwood's handbook place record lists Harris County and Montgomery County. Atascocita, Crosby, Huffman, and New Caney are marked "Is Incorporated: No" on their handbook pages. Humble is marked incorporated.

## Held for the owner (PGF-33)

The existing "delivers manufactured and mobile homes to {city}" sentence is unchanged, including the call-for-price close. It is waiting on [[OWNER: delivery area/fees per city]].

Also held, and not added here:

- delivery claims, delivery fees, or delivery areas beyond that existing sentence
- customer quotes or testimonials
- financing, land-home, used or repo homes, TDHCA
- prices

No drive time was added.
