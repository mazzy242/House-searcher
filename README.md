# Edinburgh House Browser

A map of every house for sale on ESPC in the Edinburgh area. For each home it shows:

- **How long it takes to get to Waverley station**, door to door (bus/tram, train, walk or cycle).
- **Which secondary school catchment it's in**, with the top 10 schools highlighted.
- **SIMD deprivation decile** of the neighbourhood.
- **How the price compares** to the median asking price in its postcode district.

Filter by price, bedrooms, detached, garage, time to Waverley, school catchment and SIMD. Overlay tram lines (existing and proposed), main bus routes, school catchments and SIMD.

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
    ├─ Tram + Lothian bus routes .... OpenStreetMap (Overpass API)
    ├─ Bus/tram timetable ........... BODS Scotland GTFS → travel time to Waverley
    ├─ Listings ..................... espc.com (polite scrape, new/changed listings only)
    └─ enrich + write public/data/*.json
  commit data → vite build → GitHub Pages
```

The site is static (Vite + TypeScript + MapLibre GL, OpenFreeMap basemap), so hosting is free and nothing needs an API key.

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
- **Top-10 schools.** Check the ranking in `pipeline/config/top_schools.json` against the [ESPC article](https://espc.com/news/post/top-10-secondary-schools-in-edinburgh-and-their-catchment-areas), then set `"verified": true`.
- **Proposed tram routes.** `pipeline/config/tram_proposed.geojson` is hand-traced and indicative. Edit it as plans firm up.
- **Main bus routes.** `bus.main_routes` in `settings.json` controls which routes show by default.

## Garage / detached detection

- **Detached:** "detached" (but not "semi-detached") in the listing's title, type or features.
- **Garage:** "garage" in the title, features or description, and not "no garage".

Always check the listing itself.

## Development

```
npm install && npm run dev                           # site on http://localhost:5173
python pipeline/make_sample.py                        # regenerate sample data
python -m pytest -q pipeline/tests                    # pipeline tests
python pipeline/build.py --skip-espc                  # refresh layers + journey times only
```

## Data sources and terms

- Listings © ESPC, scraped once a day for personal use. The scraper honours robots.txt and spaces its requests out.
- SIMD 2020 © Scottish Government (OGL).
- Catchments © City of Edinburgh Council (OGL). Confirm with the council before relying on them.
- Timetables: Bus Open Data Service (OGL).
- Routes © OpenStreetMap contributors (ODbL).
- Basemap © OpenFreeMap / OpenMapTiles / OSM.
