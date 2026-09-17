# Australian visa & permanent residency dashboard

A static dashboard of Australian visa grants, permanent migration program
outcomes and citizenship conferrals, built from Department of Home Affairs data
published on [data.gov.au](https://data.gov.au/data/organization/immi).

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

## Status

The chart rendering, shaping, failure handling and empty states are tested and
were rendered and reviewed in a browser. **The fetch layer has not yet run
against live data.gov.au** — the environment this was built in only permits
egress to GitHub, so the dataset slugs, resource names and column patterns in
`sources.py` are drawn from the published catalogue rather than verified against
downloaded files.

The first workflow run is the real test. Expect some series to come back
unavailable on that run; each failing card names the dataset and the columns it
actually found, which is what you need to correct the matching patterns. Run the
workflow manually from the Actions tab rather than waiting for the cron.
