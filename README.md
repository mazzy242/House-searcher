# Edinburgh House Browser

A map of the **houses** (not flats) with **2 or more bedrooms** for sale on ESPC in Edinburgh and nearby commuter towns: Midlothian, East Lothian, West Lothian (Linlithgow to Livingston) and the Fife towns on the train line (North Queensferry to Burntisland). For each home it shows:

- **How long it takes to get to Waverley station**, door to door (bus/tram, train, walk or cycle).
- **Which secondary school catchment it's in**, with the top 10 schools highlighted.
- **Which primary school catchment it's in** (City of Edinburgh), with each primary's attainment score.
- **SIMD deprivation decile** of the neighbourhood.
- **How the price compares** to the median asking price in its postcode district.
- **Auction lots** from Future Property Auctions and Auction House Scotland, alongside the ESPC listings (orange-ringed pins, with opening bid / guide price and auction date).
- **Seller situation**: "Motivated seller" (repossession/heritable creditor, executry, trustee, cash buyers), "Needs work", "Closing date set" and "Price reduced".
- **Plots & land**: building plots and land for sale (ESPC and auctions) are kept separately; a Homes / Plots & land / Both switch shows them, with plot size in acres where given.
- **EPC band** from the ESPC listing itself (shown when the agent gives it). The Scottish Government's EPC open data can also fill in bands and m² (`epc.enabled` in settings; off for now because that site blocks GitHub's servers).

Filter by price, bedrooms (min and max), bathrooms, floor area, house type (detached, semi, terraced...), garage, time to Waverley, area, secondary and primary catchment, SIMD, sale type (estate agents / auctions), seller situation, or search for a street, town or postcode.

**Saved searches and lists.** Save the current filters under a name (e.g. "Maz", "Nichelle") and switch between them with one tap. Put homes on Shortlist, Viewing, Rejected or your own lists from the detail panel; rejected homes are hidden unless you ask for them, and homes that leave ESPC stay on your lists as "no longer listed". These live in the browser (localStorage), so each phone or laptop has its own; **Copy share link** packs them into a link that adds them to another browser. Overlay tram lines (existing and proposed), main bus routes, school catchments and SIMD.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Edinburgh House Browser   312 of 1,204 homes · median £395k    [Map][List] │
├──────────────────┬─────────────────────────────────────────────────────────┤
│ Price  Beds      │                                          ┌────────────┐ │
│ ☑ Detached       │   ● £545k      ━━ tram   ┄┄ proposed     │ £545,000   │ │
│ ☑ Garage         │        ●   ▒▒ top-10 catchment           │ 4-bed det. │ │
│ ≤ 30 min to      │  ●  (3)        ░░ SIMD                    │ 🚆 24 min  │ │
│   Waverley       │         🚆 Waverley                       │ SIMD 10/10 │ │
│ Layers / legend  │                                          │ Royal High │ │
│ Avg price table  │                                          └────────────┘ │
└──────────────────┴─────────────────────────────────────────────────────────┘
```

## How it works

```
GitHub Actions (daily 05:17 UTC)
  pipeline/build.py
    ├─ SIMD 2020 data zones ......... maps.gov.scot ArcGIS service
    ├─ Secondary school catchments .. City of Edinburgh Council ArcGIS (bundled c.2014 copy as fallback)
    ├─ Primary school catchments .... City of Edinburgh Council ArcGIS + pipeline/config/primary_scores.json
    ├─ Tram, bus + rail lines ....... OpenStreetMap (Overpass API), refreshed weekly
    ├─ Bus/tram timetable ........... BODS Scotland GTFS → travel time to Waverley
    ├─ Listings ..................... espc.com (polite scrape, new/changed listings only)
    ├─ Auction lots ................. Future Property Auctions, Auction House Scotland catalogues
    ├─ EPC certificates ............. statistics.gov.scot domestic EPC extract (cached 30 days)
    ├─ duplicate check .............. pipeline/dedupe.py (ESPC is always the main record)
    └─ enrich + write public/data/*.json
  commit data → vite build → GitHub Pages
```

The site is static (Vite + TypeScript + MapLibre GL), so hosting is free and nothing needs an API key. There are three base maps, switchable on the map: Light and Roads (OpenFreeMap vector tiles) and Satellite (Esri World Imagery with road and place-name overlays).

### Time to Waverley

- **Bus/tram:** reverse connection-scan over the real weekday timetable. For arrival times 08:30, 08:40 … 09:10, it works out the latest time you could leave home and still arrive, then averages the door-to-door time. That includes walking to the stop (up to 1.2 km), changes, and walking from the stop to the station.
- **Train:** estimated from `pipeline/config/rail_stations.json` (walk + half the peak interval + minutes on the train).
- **Walk / cycle:** straight-line distance × 1.3 at 4.8 km/h and 15 km/h.
- The headline figure is the fastest of these. All the assumptions live in `pipeline/config/settings.json`.

### Average prices

These are **asking** prices of current listings, as medians by postcode district (EH4, EH10…). Each home is compared with the median for the same number of bedrooms in its district, when there are at least 3 such listings.

## Setup (one-off)

1. **Enable Pages:** repo **Settings → Pages → Source: GitHub Actions**. Pages on a private repo needs a paid GitHub plan; otherwise make the repo public.
2. **Run it:** **Actions → Refresh data and deploy → Run workflow**. The first run takes a while, because it fetches every listing once. Later runs only fetch new or changed listings.
3. The site URL appears on the workflow run and in Settings → Pages.

Until the first successful refresh, the site shows clearly-labelled **sample data**.

## Things to check after the first run

- **ESPC parsing.** ESPC's markup couldn't be inspected when this was written, so the parser uses several strategies: JSON-LD, embedded app state, meta tags, then text. If a run fails with "no property links" or "could not parse", download the `espc-debug` artifact from the run. It holds the raw HTML so the parser (`pipeline/espc.py`) can be adjusted. You can also change `espc.search_urls` in `settings.json` to any ESPC search URL you like, such as one with your own filters.
- **ESPC blocking GitHub.** If ESPC blocks requests from GitHub's servers, run the pipeline on your own machine and push:
  ```
  pip install -r pipeline/requirements.txt
  python pipeline/build.py
  git add public/data state && git commit -m "Data refresh" && git push
  ```
- **Top-10 schools** come from the [ESPC article](https://espc.com/news/post/top-10-secondary-schools-in-edinburgh-and-their-catchment-areas) (31 Aug 2026, Sunday Times league tables), including ESPC's price stats per catchment. #4 St Thomas of Aquin's is Roman Catholic, so its catchment is drawn as an orange dashed outline. Update `pipeline/config/top_schools.json` when ESPC publishes a new list.
- **Primary schools.** Catchments come live from the council (`sources.primary_catchment_layers`). Scores are in `pipeline/config/primary_scores.json`: the average share of P1, P4 and P7 pupils meeting the expected level in reading, writing, numeracy and listening & talking (Scottish Government ACEL data, via [datamap-scotland](https://datamap-scotland.co.uk/primary-school-league-tables-by-local-authority/edinburgh-city-primary-schools-ranks/)). Schools at 95% or more count as "top primaries". Update it each December when the new figures come out. The Sunday Times primary league table (out of 400) uses the P7 figures from the same data, weighted for deprivation, but is paywalled.
- **Proposed tram routes.** `pipeline/config/tram_proposed.geojson` is hand-traced and indicative. Edit it as plans firm up.
- **Main bus routes.** `bus.main_routes` in `settings.json` controls which routes show by default.

## What gets included

In `pipeline/config/settings.json` under `espc`:

- `allowed_postcode_districts` lists the areas to keep, grouped by region. Add or remove districts (e.g. `EH48` for Bathgate) to widen or narrow the search.
- `min_bedrooms` is the minimum number of bedrooms (2).
- `exclude_types_regex` sets which property types are dropped: flats, apartments, maisonettes, penthouses, duplexes, studios and retirement flats.

Most unwanted listings are skipped straight from the search results (ESPC's URLs end in the postcode, and each result card says e.g. "2 bed first floor flat"), so they never cost a page fetch. School catchments cover the City of Edinburgh only.

## Duplicates

The same home can turn up twice: re-listed on ESPC under a new number, or sold at auction as well as through an agent. `pipeline/dedupe.py` treats two listings as the same home when they share a postcode, the same house number(s) and the same street (or, for named houses, the same name). House numbers are exact: 70 and 70a are different homes.

- **Same home on ESPC twice:** the newest listing is kept, with the older price history and a "Re-listed" badge.
- **Same home on ESPC and at auction:** the ESPC listing is kept, with "Also at auction" and a link to the lot; the auction pin is removed.
- **Auction lot covering more than the ESPC home** (e.g. a lot of "70 and 70a" where 70 is on ESPC): both stay on the map, each linking to the other.
- **Same lot at two auction houses:** one pin, showing the earliest auction.

New-build house types at one development ("The Fulton, Edgelaw View") are never merged. Every decision is listed under `duplicates` in `public/data/meta.json`.

Several homes can share one map point (ESPC puts every plot of a development on the same spot); clicking there lists them all.

## Garage / detached detection

- **Detached:** "detached" (but not "semi-detached") in the listing's title, type or features.
- **Garage:** "garage" in the title, features or description, and not "no garage".

Always check the listing itself.

## Development

```
npm install && npm run dev                           # site on http://localhost:5173
python pipeline/make_sample.py                        # regenerate sample data
python -m pytest -q pipeline/tests                    # pipeline tests
npm test                                              # website tests (filters, saved lists)
python pipeline/build.py --skip-espc                  # refresh layers + journey times only
```

## Data sources and terms

- Listings © ESPC, scraped once a day for personal use. The scraper honours robots.txt and spaces its requests out.
- SIMD 2020 © Scottish Government (OGL).
- Catchments © City of Edinburgh Council (OGL). Confirm with the council before relying on them.
- Primary attainment: Scottish Government, Achievement of Curriculum for Excellence Levels (OGL).
- Timetables: Bus Open Data Service (OGL).
- Routes © OpenStreetMap contributors (ODbL).
- Base maps © OpenFreeMap / OpenMapTiles / OSM. Satellite imagery © Esri, Maxar, Earthstar Geographics (Esri's terms allow non-commercial use with attribution).
