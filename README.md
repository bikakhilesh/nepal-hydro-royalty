# Nepal Hydropower Ledger

Scrapes the Department of Electricity Development's Royalty Management System
(`rmsdoed.gov.np`), reconciles it, and renders an interactive dashboard.

Two files do everything: **`hydro.py`** (all Python) and **`template.html`**
(all markup, CSS and JavaScript).

## Live

| | |
|---|---|
| **Dashboard** | https://bikakhilesh.github.io/nepal-hydro-royalty/ |
| **Terrain map** | https://bikakhilesh.github.io/nepal-hydro-royalty/terrain.html |
| **Fleet map (SVG)** | https://bikakhilesh.github.io/nepal-hydro-royalty/fleet-map.svg |
| **Payload (JSON)** | https://bikakhilesh.github.io/nepal-hydro-royalty/dashboard.json |

## What is in here

Every royalty filing the Department of Electricity Development publishes: 198
plants, ~14,000 monthly rows, energy and capacity royalty, generation, revenue
and PPA rates, back to the start of the register. Reconciled rather than merely
collected — royalty is verified at exactly 2.00% of the revenue base, and
`Previous Due + Energy + Capacity − Received = Balance` holds to the paisa,
which is what makes it safe to build on.

Alongside it, from RPGCL's own network sheet (4 Sep 2022, its newest public
version): 188 substations, its 132/220/400 kV corridors by build status —
existing, under construction and proposed all read off the sheet's own layer
tags, not guessed from colour — cross-border interconnection to India, 96
small-hydro clusters, and 248 pipeline projects totalling **19,756 MW** against
a built fleet of **3,758 MW**.

## Install

```bash
pip install -r requirements.txt
```

Python 3.10+. Only the reconciliation section needs anything else: point
`HYDRO_XLSX` at an operator's quarterly income statement and it appears,
otherwise it is skipped silently.

## Rebuild the dashboard

```bash
python hydro.py
```

Reads the CSVs, recomputes every figure, and writes `nepal_hydro.html` by
substituting the JSON payload into `template.html`'s `__PAYLOAD__` placeholder.
No network access. Byte-identical across runs.

**Edit the page in `template.html`, never in `nepal_hydro.html`** — the latter is
generated and overwritten on every build.

### Map exports

```bash
python extract_rpgcl.py   # RPGCL 2040 PDF -> np_grid_plan.json (planned corridors)
python hydro.py mapsvg    # standalone SVG: rivers + peaks + fleet, self-contained
python hydro.py map3d     # MapLibre map on real elevation tiles  -> LOCAL FILE ONLY
```

`map3d` writes `nepal_fleet_3d.html` from `map3d.tpl.html`. **It cannot be
published as an Artifact** — the Artifact CSP blocks every external host, so its
script and tile requests fail and the map renders blank. Open it from disk or
serve it over localhost. Tile sources are keyless (OpenStreetMap raster +
Mapzen/AWS terrarium DEM); both have usage policies, so don't point heavy traffic
at them. It needs an internet connection at view time.

Two gotchas worth keeping. **Avoid data-driven paint expressions under terrain**
— MapLibre re-projects every vector feature each frame, and per-feature `step`
expressions across 409 grid lines froze the renderer outright. One static layer
per voltage class renders fine. And sources and layers are added on **`style.load`**, not
`load`. With `terrain` declared in the initial style MapLibre waits on the DEM
source and may never fire `load`, so a `load` handler silently never runs — the
basemap renders and your own layers just never appear.

## Refresh the data

```bash
python hydro.py latest    # the two newest fiscal years  396 GETs, ~15 s
python hydro.py scrape    # energy + royalty detail   3,762 GETs, ~2.5 min
python hydro.py meta      # plant register              198 GETs, ~40 s
python hydro.py geo       # OSM + Wikidata + districts + outline + positions
python hydro.py all       # all of the above, then build
python hydro.py --stats   # summary only, write nothing
```

`scrape` re-cleans automatically; `geo` re-resolves plant positions. Sub-steps
`clean` and `coords` can be run on their own.

The revenue reconciliation in section 06 needs the operator's reported revenue,
which comes from a workbook at `HYDRO_XLSX`. That workbook is private input and
is not in this repo, so **only its output is cached**, in `recon_reported.json`:
the published cumulative quarterlies, about twenty numbers. Anyone can rebuild
the section from that, workbook or no workbook.

Only the *reported* half is cached, deliberately. The RMS half is recomputed
from live scraped data on every build, so a monthly refresh moves the comparison
rather than freezing a snapshot of it — which matters, because a comparison that
cannot move is not evidence of anything. With the workbook present the cache is
rewritten, so it cannot drift from the source.

The page still has to survive the cache being absent, and it did not at first:
`reconTable()` read `D.recon.median_abs_pct` unconditionally, so a build without
recon data threw before any chart was drawn and the site served a page of empty
panels. Section 06 now drops out cleanly and the rest renumber. A local build
could never have caught it, because the workbook is always there.

## Automation

Two workflows keep the published site current without anyone running anything.

**`.github/workflows/refresh-data.yml`** — re-scrapes RMS, rebuilds the
dashboard and both maps, and commits whatever changed. Daily at 03:17 UTC, plus
a full-history pass on the 5th of each month, or on demand from the Actions tab.

**The daily run fetches only the two newest fiscal years — 396 requests, not
3,762.** A filed year never changes again, so re-reading nineteen of them to
learn about one is the expensive way to find nothing; `hydro.py latest` scrapes
the newest years and merges them over what is on disk.

Two years rather than one, because of the year boundary. When Shrawan opens a
new fiscal year the dropdown's newest entry moves while the year that just
closed is still being filed — its Jestha and Asar rows land weeks late — and
following only the newest entry would walk away from them. As of Aug 2026 the
register already lists 083/84 with no filings in it yet, so a one-year window
would have been scraping an empty year and nothing else.

A **new plant** is picked up without any special handling: the plant list is
re-read from the site on every run, and a plant that has just begun generating
can only have filings in those same newest years. The register pass gives it a
licence, company, district and commissioning date; `coords` then places it on
the map in the same build, which is why that step runs every time — it is
offline, reading the cached OSM/Wikidata candidates, so it costs nothing.
Full `geo` stays opt-in because it calls Nominatim, whose policy caps you at
one request a second.

What the daily pass **cannot** see is a correction to an older year. That is
what the monthly full re-scrape is for.

### Nothing may disappear

A scrape that half-fails still writes perfectly well-formed CSVs — just short
ones. Two mechanisms stop that reaching the repo.

**Every write is a merge keyed on what came back**, never on what was asked
for. `_merge_on()` replaces only the rows whose key is in the response set and
leaves the rest untouched, so a failed request keeps its subject rather than
deleting it. This applies to the full scrape and the plant register too, not
just the incremental pass — the full run is the one that is supposed to repair
the data, and it was the least protected of the three. The deliberate
consequence: a row can only be removed by a *successful* fetch that returns
nothing for it, so a plant delisted at source keeps its history here.

**`.github/scripts/guard.py` then checks that nothing vanished** — every
plant-year and every plant present in the committed data must still be present.
Growth is fine; disappearance never is. A row-count floor cannot do this job: at
14,142 rows a 95% floor tolerates 707 missing rows, about **twelve entire plant
histories**, so a handful of plants could evaporate and the job would still go
green. The floor is kept as a second, coarser net for failures that change a
file's shape rather than its keys.

It checks the **cleaned** files, not the raw ones, and an early CI run is why:
it flagged `rms_summary.csv` at 39% and was wrong to. The raw summary carries a
placeholder row for every plant-year with no filing, the original scraper wrote
those and the current one drops them, so the guard was comparing formats rather
than data.

**`.github/workflows/pages.yml`** — publishes to GitHub Pages on every push
that touches a built file. The terrain map is the reason this exists: it needs
external tile hosts, which the Artifact CSP blocks outright, so Pages is the
only place all three views render.

## Sources and attribution

- **Royalty, generation, revenue, PPA rates** — Department of Electricity
  Development, Royalty Management System (`rmsdoed.gov.np`). Public filings.
- **Planned corridors, substations, pipeline projects** — Rastriya Prasaran
  Grid Company, *Nepal Transmission Network Map*, 4 Sep 2022.
- **Built grid geometry** — OpenStreetMap contributors, ODbL.
- **Plant coordinates** — OpenStreetMap and Wikidata, resolved by name.
- **Terrain and basemap tiles** — OpenStreetMap raster and Mapzen/AWS terrarium
  DEM, both keyless and both with usage policies. Don't point traffic at them.

The code here is mine; the data is the publishers'. Nothing in this repo is an
official record — read it against the source before relying on a number.

## Data traps handled in code — do not "fix" these by hand

- **Two Bikram Sambat date formats.** `MitiofOperation` is `DD/MM/YYYY` on 195
  plants and `YYYY/MM/DD` on 3. `bs_year()` takes whichever component is a
  plausible 4-digit BS year. Assuming a position silently voids every plant age
  and collapses the 15-year finding.
- **Footer bleed.** The page's summary block sits inside the same HTML table as
  the data; only rows with a `yyyy/m` stamp are kept (drops 5,876 of 20,133).
- **Three table schemas, one of them USD.** Plants report either *Billed Amount
  to NEA* or *Invoice to NEA (NRS)*; foreign-currency PPAs (e.g. Upper Bhotekoshi)
  report *Invoice to NEA (USD)* with a monthly *Exchange Rate*, plus any NPR
  portion of the same invoice. All are coalesced into `Revenue_NPR`. Ignoring the
  USD schema drops those plants' revenue entirely — it cost NPR 18 bn on one plant.
  USD revenue is *reconstructed* (USD x fx + NRS portion), so its implied royalty
  rate lands at 1.88–1.91% rather than exactly 2.00%; treat it as accurate to
  about a percent, not to the paisa.
- **Annual filings.** Some NEA plants file a whole year as one row, giving
  impossible monthly capacity factors of 7–10×. Flagged `IsAnnualFiling`,
  excluded from seasonality, kept in annual totals.
- **Column names carry data.** The header row embeds each plant's Previous Due
  value, so bare numeric tokens are stripped from column names.
- **Two register fields are unedited defaults and are ignored.** Every populated
  latitude/longitude reads `39.7817,-89.6501` (Springfield, Illinois);
  `Province 1` is carried by 97 plants including many demonstrably in Bagmati or
  Gandaki. Districts cross-check correctly and are used instead.
- **Balance is a stock, not a flow.** `Previous Due + Energy + Capacity −
  Received = Balance`, carried forward — never sum it across years.
- **Capacity royalty of zero means "not assessed yet", not "tier 1."** Common in
  the current fiscal year.
- **Positions carry four confidence levels.** `plus` (68) is a location the
  author looked up on Google Maps one plant at a time and checked against
  imagery — the only positions here a person verified rather than a matcher, so
  they outrank everything else. `exact` (31) is a name match to a surveyed
  OSM/Wikidata point. `river` (48) means the register names the plant's river,
  that river is mapped, and the marker sits on it — right watercourse, arbitrary
  point along it. `district` (31) is the bare centroid. 20 have neither.
- **Plus codes are short codes, so they were decoded and then checked.** A short
  code (`G4R7+5M7`) omits the leading characters and is only meaningful next to a
  reference point; each was recovered against its own district centre and then
  tested — inside Nepal, inside a plausible radius of that district, and, where
  OSM names the watercourse the register gives, close to it. Two of the 70 failed
  and were dropped: both repeated the row above's code, putting `Solu` 87 km from
  any Solu Khola and `Sipring Khola HP` 40 km outside Dolakha.
- **A cascade files every station under one district on one river.** Snapped
  naively they all take the same nearest vertex and draw as a single dot only one
  of which can be clicked — six of the eight Mai Khola stations were stacked on
  one point. The snap now keeps a ledger per river and spreads them at least
  800 m apart down the reach. The point along the river was always arbitrary, so
  this costs no accuracy and is the difference between seeing six plants and one.
- **Most of the fleet is on a khola, and OSM does not call those rivers.** A
  `waterway=river` query returns the main stems and nothing else, so at any length
  threshold it misses the tributary most stations actually sit on. `fetch_terrain`
  runs a second pass over `waterway=stream` and keeps only the 36 named streams a
  plant in the register is named for — Nepal has about 1,460, so the filter is what
  makes embedding them affordable. They are what lets 112 of the 178 plotted plants
  be attributed to a named watercourse at all, across 54 of them.
- **How far a plant may be snapped is bounded by the river's own length.** Khola
  names repeat all over Nepal — a 5 km `Pikhuwa Khola` found 39 km from the district
  the plant is filed under is a *different* Pikhuwa Khola. The snap radius is
  `min(30 km, max(10 km, mapped length))`, so a main stem can pull a marker further
  than a creek can. Without it the stream pass looked like it placed 81 plants on
  their river; 22 of those were coincidences of a common name. A hollow dot reading
  "district only" is a better answer than a filled one on the wrong stream.
- **The register sometimes names two rivers in one field.** `Kabeli Khola, Amji
  Khola` is a plant drawing from both. The field is split on its punctuation and
  tried in written order, so the river the register puts first wins — reducing the
  whole field to a token set picks between them arbitrarily.
- **OSM names one river several ways along its length.** `Kali Gandaki` and
  `Kali Gandaki River` are one river in two rows; `Seti Nadi` / `Seti Khola` /
  `Seti River` / `Seti Gandaki River` are one river in four. The map merges them
  on the normalised core name and labels the group with its longest variant —
  otherwise selecting a river would mean selecting which spelling of it you meant.
  Segments are then stitched end to end (968 OSM ways → 430 polylines), which is
  what gives a label a continuous run to sit along.
- **Coordinate matching is deliberately strict.** Sibling stations differ by one
  qualifier (`Upper`/`Lower`, an ordinal, a size class), so both the core name
  and the qualifier set must agree, and a candidate claimed by two plants is
  demoted. A loose fuzzy match silently invents locations.
- **Revenue can be negative.** Billing credits appear as negative months.
- **The RPGCL 2040 map georeferences exactly, and every feature on it carries its
  own classification — read from the sheet itself, not guessed.** `extract_rpgcl.py`
  reads the printed projection (MUTM Everest 1830, TM, CM 84E, k=0.9999, FE
  500,000) and fits the printed coordinate graticule: 141.11 m/pt, worst residual
  **14.5 m** on a 1:400,000 sheet. The PDF is an ArcMap export, and PyMuPDF
  exposes the ArcMap layer name on every drawn path (`get_drawings()`) and every
  text/marker glyph (`get_texttrace()`) as `"layer"` — so a corridor's kV and
  build status, and a marker's category, come from that name directly. This
  replaced an earlier version that matched on (stroke colour, stroke width)
  instead, which could not tell the 132 kV family apart at all — existing, under
  construction and proposed all draw in the same red at the same width, and the
  colour approach left the whole family out. Layer-based extraction recovers it:
  **existing 190, under construction 489, proposed 2,566 segments**, plus 465
  segments of cross-border 400 kV interconnection to India (`cross_border_line_400kv`),
  also previously unhandled. A corridor is still drawn twice within its own layer —
  ArcMap's usual halo-plus-centreline symbol — so only the dominant (colour, width)
  pair per layer is kept, same exactness as the old approach, just auto-detected
  per layer instead of hand-picked once for the whole page. That also fixed a real
  misattribution: ~19 segments near Bajhang sat on an unnamed ArcMap layer
  ("Other 5") that happened to share proposed-400's exact colour and width, and
  the old colour-only match counted them as proposed 400 kV corridor when the
  sheet's own legend makes no such claim for them. Validation: extracted 400 kV
  corridors still pass through Lapsiphedi, Hetauda, Dhalkebar, Butwal and Duhabi,
  Nepal's real 400 kV nodes.
- **Only the PIPELINE is taken from the RPGCL sheet, never operating plants.**
  248 projects that do not yet generate — 126 survey licence, 40 government
  reserved, 33 construction-licence application, 27 construction licence, 20 under
  construction, 2 survey-licence application — totalling **19,756 MW**, against a
  built fleet of 3,758 MW. These cannot appear in RMS by definition: a project
  that does not generate pays no royalty. Capacity is parsed from the sheet's own
  "Name (MW)" labels for 168 of them. The 355 IN OPERATION markers are excluded:
  see below.
- **A small-hydro cluster is a different thing from a pipeline project, and the
  sheet marks it as one.** 96 points on the `HPP_CLUSTER_BELOW_20_MW` layer, each
  a *group* of several sub-20 MW projects too close together at this map's scale
  to plot individually — labelled with the group's **combined** capacity, not one
  project's. Missed entirely before this pass; now its own marker (a downward
  triangle, so it is never mistaken for one project).
- **The RPGCL sheet's OPERATING plants are deliberately NOT merged.** It carries
  355 markers for plants in operation, but the sheet is dated Sep 2022 while the
  RMS register runs to FY 082/83 and is the authoritative royalty source. The
  overlap is also poor: georeferenced, only 42 of 355 land within 3 km of an RMS
  plant and only 29 of 178 RMS plants find a match — merging them would have put
  two near-duplicate sets of dots on the map at inconsistent positions.
- **Substations come from the same sheet, matched by layer rather than colour and
  position.** They are `"` characters in the `ESRIDefaultMarker` font, on one of
  three layers named exactly for their status — `EXISTING_S/S`, `UNDER_CONSTRUCTION_S/S`,
  `FUTURE_S/S` — so no legend-swatch position filter is needed at all: a swatch
  never carries a real layer name. Switching from `get_text("dict")` to
  `get_texttrace()` for this pass fixed a real, confirmed drop: one future
  substation near Nepal's eastern border (26.65°N, 88.40°E) was silently missing
  from the `dict` extraction — `get_texttrace()` decomposes to one entry per
  glyph and never merges markers into a shared span the way `dict` occasionally
  does. Counts: red existing (74), green under construction (25), blue future
  (**89**, was 88) — **188** in total, against OSM's 107. Names are the nearest
  label line on the sheet and are **best-effort**: where several markers share
  one nearest label (dense Kathmandu), the name is kept only for the closest and
  dropped for the rest, so 168 of 188 carry a name and none is duplicated across
  distinct substations.
- **The RPGCL map is still a picture for everything else.** `rpgcl.com`'s network
  PDF (5904x3456 pt, 713 image objects) names substations but carries no
  coordinates, so grid geometry comes from OpenStreetMap `power=line` /
  `power=substation` instead — 409 lines (400/220/132/66 kV) and 107 substations.
- **The sheet is dated 4 Sep 2022 and is the newest public version RPGCL has
  published.** Anything commissioned, reconductored (HTLS) or re-routed since
  then will not show on it; the dashboard's own map note says so explicitly
  rather than let the vintage go unstated.
- **USD takes precedence in the revenue waterfall.** On a mixed invoice
  *Invoice to NEA (NRS)* holds only the NPR *portion*; letting it win reports a
  fraction of the month (Upper Bhotekoshi showed NPR 11.8m against a USD 2.94m
  invoice). A filed USD invoice of **zero** means "not billed in USD this month",
  not "no revenue", so it must fall through to the NPR columns.
- **`Seasional Rate (USD)` is a mixed column.** Values below 1 are genuinely
  USD/kWh (0.00–0.12); values at or above 1 are Nepali NPR tariffs (4.80, 5.09,
  5.23, 8.40, 9.16) mis-filed into the USD field. The two populations do not
  overlap, so they are split on magnitude and routed to separate columns.
- **Total generation is rebuilt from its components when blank.**
  `Total = Metered + Additional + Internal + Transmission loss` reconciles exactly
  on 96.5% of rows where both exist; using it recovers 74 months.
- **`fails=` in the scrape log is mostly not failures.** 2,293 of the 3,762
  plant-year requests return no table, because the register simply holds no
  filing for that plant in that year — a plant commissioned in 076/77 has
  nothing for 066/67. The counter cannot tell those apart from a real HTTP
  error, so a healthy full scrape reports ~61% "failures" and still produces
  the complete 1,469 plant-years. Judge a run by the cleaned row counts.
- **The raw scrape used to be nondeterministic, and it mattered.** The thread
  pool yields in completion order, which varies run to run, so `rms_monthly.csv`
  rewrote itself almost entirely on every scrape even when not one figure had
  changed — a 14,000-line diff reporting nothing. Rows are now sorted to a fixed
  key before writing, and two consecutive network scrapes produce byte-identical
  files. Without that, a daily job commits megabytes of reshuffling as if it
  were news.
- **`Received` moves within the day.** It is the only field that does. A
  latest-years scrape an hour after a full one found 83 plants with different
  `Received` and `Balance` figures for 082/83 and everything else untouched —
  royalty receipts land continuously, so the current year is genuinely live in a
  way the closed years are not.
- **One known source error.** Upper Bhotekoshi 2083/02 files a USD invoice of
  22.94m against a ~2.9m trend; its own royalty implies ~2.95m. It is left as
  filed rather than silently corrected — it inflates that plant's latest year.

## Scraping etiquette

GET only. The plant register's edit form is read but never submitted — its POST
action and CSRF token are untouched. Nominatim is called at most once per second.

## Layout

```
hydro.py          everything: scrape, clean, geo, match, payload, render
template.html     page markup, CSS, JS; __PAYLOAD__ is substituted at build
nepal_hydro.html  generated — do not edit
dashboard.json    generated payload, handy for inspection
rms_*.csv         scraped and cleaned data
np_*.json         districts and national outline
geo_candidates.json  merged OSM + Wikidata coordinate candidates
recon_reported.json  the operator's published quarterlies, cached so section 06
                     builds without the private workbook
```
