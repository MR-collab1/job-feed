# A&E alcohol withdrawal audit: CIWA-Ar scoring, treatment and outcomes

A clinical audit of Emergency Department attendances by patients presenting with
alcohol intoxication or withdrawal. It merges the two source workbooks into one
attendance-level dataset, exposes the findings as a REST API, and builds a
PowerPoint deck from the same numbers.

The questions it answers:

- How many people were treated, and over what period?
- Was the CIWA-Ar score repeated, and for how many patients?
- For a given score, how many attendances had it and how many of those were treated?
- How many were admitted, and how many self-discharged?

## Headline findings

Audit window 31 October 2025 to 26 February 2026: 129 attendances by 119 patients.

| Finding | Figure |
| --- | --- |
| CIWA-Ar score documented | 18 of 129 (14.0%) |
| Received withdrawal treatment | 46 of 129 (35.7%) |
| Treated with no CIWA score recorded | 29 of 46 (63.0%) |
| Score repeated | 1 of the 18 scored attendances |
| Admitted | 30 (23.3%) |
| Self-discharged | 24 (18.6%) |

## How the two workbooks are merged

`Audit_CIWA.xlsx` (sheet `Feuille 1`) is the master attendance list for the whole
window. Its clinical columns are completed for the earlier attendances and left
blank for the later ones. `AE_PresentingComplaint_39_2.xlsx` **sheet 3 only** is
the second round of case-note review, and fills in exactly those blanks.

Both sheets carry the same 19 columns. They are joined on **patient ID plus
arrival date and time**, not patient ID alone, because several patients in this
cohort re-attend within days. Where both describe the same attendance, the
case-note review wins and the master sheet fills any gap it leaves. On the
current data all 61 review rows match an existing attendance, so nothing is
duplicated and nothing is lost.

Sheets 1 and 2 of the presenting-complaint workbook cover a different period and
are deliberately ignored.

## Definitions that change the numbers

**Treated** means a benzodiazepine or thiamine (Pabrinex) was given: the drugs
that treat alcohol withdrawal. IV fluids alone are supportive care and are
reported separately, with an "any intervention" figure that includes them.

**Not recorded is not the same as no.** The first workbook writes an absent
answer as `-`, the second as `N/A`, and both are kept distinct from a positive
"No". Collapsing them would overstate how much of the pathway was assessed.

**Missing notes is not a clinical failure.** `case Notes missing` and `NO NOTES`
are counted as a records problem, separately from attendances where the notes
were reviewed and no score was found.

Three denominators are in play and every result states which it used: all
attendances, those whose case notes were reviewed, and those with a recorded
CIWA-Ar score.

## Getting started

```bash
pip install -r requirements.txt
cd deck && npm install && cd ..      # only needed to build the slide deck
```

Put the two workbooks in `data/` (see `data/README.md`). They are gitignored
because they hold identifiable patient records.

## Command line

```bash
python -m alcohol_audit summary                    # headline figures as text
python -m alcohol_audit report --indent 2          # every insight as JSON
python -m alcohol_audit score 18                   # one CIWA score in detail
python -m alcohol_audit deck audit.pptx            # build the slide deck
python -m alcohol_audit --from 2026-01-01 --to 2026-01-31 summary
```

## API

```bash
uvicorn alcohol_audit.api:app --reload --app-dir src
```

Interactive documentation is at `/docs`.

| Endpoint | Returns |
| --- | --- |
| `GET /health` | Liveness, cohort size and the merge report |
| `GET /api/overview` | Cohort size, period, demographics, length of stay |
| `GET /api/insights/treatment` | Who was treated, with what, over which period |
| `GET /api/insights/ciwa/documentation` | How often a score was documented |
| `GET /api/insights/ciwa/scores` | Distribution, with treatment at each score and band |
| `GET /api/insights/ciwa/scores/{score}` | One score: how many had it, how many treated |
| `GET /api/insights/ciwa/repeats` | Repeat scoring, and how many patients had one |
| `GET /api/insights/outcomes` | Admitted, discharged, self-discharged, died |
| `GET /api/insights/time-periods` | By month, weekday, arrival hour, length of stay |
| `GET /api/insights/presenting-complaints` | Complaint themes |
| `GET /api/insights/data-quality` | Gaps in the audit trail |
| `GET /api/report` | Every insight in one payload |
| `GET /api/attendances` | Attendance-level records, pseudonymised |
| `GET /api/report.pptx` | The slide deck as a download |
| `POST /admin/reload` | Re-read the workbooks from disk |

Every insight endpoint takes `date_from`, `date_to`, `outcome` and
`ciwa_documented`, so any figure can be re-cut without restarting:

```bash
curl 'localhost:8000/api/insights/ciwa/scores/18'
curl 'localhost:8000/api/insights/outcomes?date_from=2026-01-01&date_to=2026-01-31'
curl 'localhost:8000/api/report.pptx' -o audit.pptx
```

### Patient confidentiality

`/api/attendances` pseudonymises the local patient ID by default, into a stable
SHA-256 based token that cannot be reversed. Real IDs are returned only when the
caller passes `include_identifiers=true` **and** the service was started with
`ALCOHOL_AUDIT_ALLOW_IDENTIFIERS=1`, so a casually deployed instance cannot leak
them. No endpoint that aggregates data returns an identifier, and the slide deck
contains aggregate figures only.

## Slide deck

Twelve slides: cohort, method, headline findings, the four findings in turn,
time period, caveats and recommendations. Charts are native PowerPoint charts,
so they stay editable after the file is opened.

```bash
python -m alcohol_audit deck audit.pptx
```

The Python layer computes the report and hands it to `deck/build_deck.js`, which
draws the slides with pptxgenjs. Node 18 or newer is required; set
`ALCOHOL_AUDIT_NODE` if `node` is not on the path.

## Tests

```bash
python -m pytest
```

113 tests. Those needing the source workbooks skip cleanly when `data/` is empty,
so the suite runs in CI without patient data.

## Reading the results

The cohort is small and the audit measures what was written down, not what
happened at the bedside. Only 18 attendances carry a CIWA-Ar score, so per-score
figures are counts rather than rates and no statistical inference should be drawn
from them.

## Layout

```
src/alcohol_audit/
  normalise.py   free-text values to a closed vocabulary
  etl.py         load both workbooks, merge on patient plus arrival time
  analytics.py   every insight, as plain dictionaries
  api.py         FastAPI application
  deck.py        Python side of the deck build
  __main__.py    command line
deck/build_deck.js   slide generation with pptxgenjs
tests/               113 tests
```
