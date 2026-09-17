# Australian visa & permanent residency dashboard

A static dashboard of Australian visa grants, permanent migration program
outcomes and citizenship conferrals, built from Department of Home Affairs data
published on [data.gov.au](https://data.gov.au/data/organization/immi).

Dark is the default theme; the toggle in the masthead switches to light and the
choice persists per browser.

Live page: `visa/index.html` (served at `/visa/` on GitHub Pages).
Layout preview with invented numbers: `/visa/?demo=1`.

## Read this before quoting a number

- **The source data is not daily.** Home Affairs publishes on a monthly to
  annual cycle depending on the dataset. The workflow *checks* daily and commits
  only when the published figures actually change, so "updated daily" means the
  check is daily, not the statistics.
- **These are grants, not applications or arrivals.** A figure counts visas
  granted in a period — not applications lodged, people who arrived, or the
  current stock of visa holders.
- **Suppressed counts are unknown, not zero.** Home Affairs replaces small
  counts with `<5` for privacy. The pipeline treats those as missing, so
  category totals may not reconcile to a published grand total.
- **Nothing is estimated or carried forward to fill a gap.** If a series cannot
  be built, its card says so and names the reason.
- **Not every breakdown exists.** Home Affairs does not publish every
  cross-tabulation as a data file. Where a breakdown is missing, the page says
  so under "Looked for, not published" rather than approximating it.
- **The subclass chart mixes reference periods.** Programs publish on different
  cycles, so each subclass is counted in the most recent year *its own program*
  published. The card subtitle shows the span, and the table view has a Period
  column. Do not read it as a single-year snapshot.

## How it works

```
data.gov.au (CKAN)
      │  package_show / datastore_search / file download
      ▼
scripts/build_visa_stats.py ──► visa/data/latest.json ──► visa/index.html
      │                                                        │
      └── scripts/visa_stats/                                  └── assets/{charts,app}.js
            http.py       retries and timeouts
            catalogue.py  dataset + resource resolution
            tables.py     CSV/XLSX parsing, header detection
            columns.py    column-name → chart-role matching
            sources.py    the declarative series specs
            build.py      shaping and headline tiles
```

Two decisions carry most of the robustness:

**Nothing hardcodes a resource ID.** CKAN resource UUIDs change every time an
agency republishes a file. Datasets are resolved by their stable slug, with a
keyword search against the `immi` organisation as a fallback, and the resource
is chosen by matching its *name*.

**Columns are matched by pattern, not by name.** Home Affairs renames columns
between releases ("Citizenship country" → "Country of citizenship"). Each series
declares an ordered list of patterns per role, so a rename degrades into a
warning on one card instead of a crash.

## Publishing

GitHub Pages is already enabled on this repository, so the page goes live at
`/visa/` as soon as this branch reaches the default branch.

The daily workflow **cannot run before then**: GitHub only registers workflows
that exist on the default branch, so `update-visa-stats.yml` is not dispatchable
from a feature branch. Once merged, trigger it by hand from the Actions tab
rather than waiting for the 19:00 UTC cron.

## Running it

```bash
# Rebuild the published data (needs network access to data.gov.au)
python3 -m pip install requests openpyxl
python3 scripts/build_visa_stats.py

# Inspect without writing, or build a single series
python3 scripts/build_visa_stats.py --dry-run --only pr_by_citizenship -v

# Tests — offline, no network required
python3 -m unittest discover -s scripts/tests -t scripts

# Preview the page (fetch() needs HTTP, not file://)
python3 -m http.server 8000   # then open http://localhost:8000/visa/
```

A run that fetches nothing does not blank the dashboard: the previous series are
kept, `last_checked_at` moves, and the page shows a banner saying the figures are
from the last successful refresh.

## What is on the page

| Card | Shape | Source program |
|---|---|---|
| Permanent Migration Program outcomes by stream | stacked bars over time | Historical / Australian migration statistics |
| Permanent Migration Program by country of citizenship | ranked bars | Permanent migration program |
| Permanent Migration Program by state and territory | ranked bars | Permanent migration program |
| Permanent Migration Program by visa subclass | ranked bars | Permanent migration program |
| Temporary visa grants by program | multi-line over time | Student, visitor, temp work, temp graduate, WHM |
| **Visa grants by subclass** | ranked bars + program filter | All six programs above |
| Student visa grants by country of citizenship | ranked bars | Student visa program |
| Australian citizenship conferrals | line over time | Australian / historical migration statistics |
| **Citizenship conferrals by country of prior nationality** | ranked bars | Australian migration statistics |
| Citizenship conferrals by state and territory | ranked bars | *optional — see below* |

### Finding a single country

The chart shows a readable top-N, but the published payload carries every
category (up to 300) in `all_items`. The table view renders the full list with a
filter box, so a reader can type "Nepal" instead of scanning a top-15 bar chart.
Filtering only hides table rows — the chart is untouched, so a bar never changes
meaning under a filter.

Every card also offers a CSV of exactly what it is showing: ranked series export
the full ranked list, time series export the period × category matrix.

### Optional series

A spec marked `optional=True` is one we are **not certain Home Affairs publishes
as a data file**. If it cannot be built it is left off the page entirely rather
than shown as a broken card, it does not count against the run status, and it is
listed under "Looked for, not published" with the reason. The pipeline retries
it on every run, so the card appears by itself if the department starts
publishing the table.

`citizenship_by_state` is the first of these. Home Affairs reports citizenship
by residential state in some narrative publications and has released it under
FOI, but no machine-readable state-by-state conferrals table has been confirmed
on data.gov.au. The spec searches the citizenship datasets for one on every run.

**Partner visas** (309/100, 820/801) have no standalone dataset on data.gov.au.
They are reported inside the permanent migration program, so they appear in the
two subclass cards rather than a card of their own.

### The subclass card

Grants run from visitor subclass 600 in the millions down to parent visas in the
hundreds. Three things keep the small ones readable:

- every bar carries its value as a label outside the bar end, so even a 2px bar
  is legible;
- a **program filter** narrows to one program and re-bases the shares onto it;
- the table view carries the exact numbers, the program and the period.

Bars are a single colour, so filtering can never repaint a series — a reader who
learned a bar's identity keeps it.

## Adding a series

Add a `SeriesSpec` to `scripts/visa_stats/sources.py` — no new parsing code:

```python
SeriesSpec(
    id="pr_by_age",
    title="Permanent Migration Program by age group",
    subtitle="Most recent program year on record",
    shape="ranked",              # ranked | time_by_category | time_total
    unit="places",
    parts=(Part(
        dataset_slugs=("permanent-migration-program-skilled-family",),
        search_terms="permanent migration program age",
        resource_patterns=(r"age",),
        roles=(Role("category", (r"age group", r"^age$")), GRANTS),
    ),),
)
```

Then give it a card in the `DISPLAY` and `ORDER` maps in `visa/assets/app.js`.
A test fails if you forget — `test_the_page_knows_how_to_draw_every_spec` checks
that every spec has both.

A multi-part spec gives each `Part` a `label`. That label becomes the *group* on
each ranked item, which drives the program filter, the tooltip's Program row and
the per-program reference periods.

## Status

The chart rendering, shaping, failure handling, filtering and empty states are
tested and were rendered and reviewed in a browser in both themes and at phone
width. **The fetch layer has not yet run against live data.gov.au** — the environment this was built in only permits
egress to GitHub, so the dataset slugs, resource names and column patterns in
`sources.py` are drawn from the published catalogue rather than verified against
downloaded files.

The first workflow run is the real test. Expect some series to come back
unavailable on that run; each failing card names the dataset and the columns it
actually found, which is what you need to correct the matching patterns. Run the
workflow manually from the Actions tab rather than waiting for the cron.
