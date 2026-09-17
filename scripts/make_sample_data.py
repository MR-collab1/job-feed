#!/usr/bin/env python3
"""Generate visa/data/sample.json for the ?demo=1 layout preview.

The numbers are invented. They exist so the page can be designed and reviewed
before the pipeline has run, and the page shows a red banner whenever this file
is loaded. It is built through the same shaping functions as the real payload,
so the demo cannot drift away from the published schema.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from visa_stats.build import (  # noqa: E402
    SCHEMA_VERSION,
    _shape_ranked,
    _shape_time_by_category,
    _shape_time_total,
    build_headline,
)
from visa_stats.sources import MIN_PROGRAM_YEAR, SPECS  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "visa" / "data" / "sample.json"

# The dashboard reports from 2020-21 onward, so the sample starts there too.
YEARS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

STREAMS = {
    "Skilled": [79_600, 79_600, 142_900, 132_200, 124_100],
    "Family": [77_400, 52_500, 52_500, 52_500, 55_300],
    "Special Eligibility": [100, 100, 110, 300, 400],
}

TEMPORARY = {
    "Visitor": [90_000, 240_000, 2_600_000, 3_100_000, 3_250_000],
    "Student": [175_000, 188_000, 577_000, 527_000, 489_000],
    "Temporary work (skilled)": [22_000, 31_000, 80_000, 74_000, 68_000],
    "Temporary graduate": [40_000, 44_000, 94_000, 112_000, 98_000],
    "Working holiday maker": [28_000, 22_000, 180_000, 213_000, 196_000],
}

# Programs publish on different cycles, so the sample deliberately mixes
# reference periods to exercise the per-program "latest year" handling.
SUBCLASS_GRANTS = {
    "Visitor": ("2024-25", {
        "600 Visitor": 3_010_000,
        "601 Electronic Travel Authority": 190_000,
        "651 eVisitor": 48_000,
    }),
    "Student": ("2024-25", {"500 Student": 486_000, "590 Student Guardian": 3_000}),
    "Working holiday maker": ("2024-25", {
        "417 Working Holiday": 131_000,
        "462 Work and Holiday": 65_000,
    }),
    "Temporary graduate": ("2023-24", {"485 Temporary Graduate": 112_000}),
    "Temporary work (skilled)": ("2023-24", {
        "482 Skills in Demand": 71_000,
        "494 Regional Employer Sponsored": 3_100,
    }),
    "Permanent migration": ("2024-25", {
        "820/801 Partner (onshore)": 31_700,
        "189 Skilled Independent": 30_400,
        "309/100 Partner (offshore)": 20_900,
        "190 Skilled Nominated": 24_100,
        "491 Skilled Work Regional": 22_600,
        "186 Employer Nomination": 21_800,
        "143 Contributory Parent": 6_800,
        "858 Global Talent": 4_000,
        "187 Regional Sponsored": 3_900,
        "804 Aged Parent": 900,
    }),
}

CITIZENSHIP_BY_PRIOR_COUNTRY = {
    "India": 41_200, "United Kingdom": 22_800, "China": 16_400,
    "Philippines": 14_900, "New Zealand": 12_600, "Nepal": 9_800,
    "Pakistan": 8_400, "Vietnam": 7_900, "South Africa": 6_700,
    "Sri Lanka": 6_200, "Iraq": 5_100, "Afghanistan": 4_800,
    "Malaysia": 4_300, "Iran": 4_100, "South Korea": 3_600,
    "Bangladesh": 3_300, "Brazil": 2_900, "Indonesia": 2_700,
}

CONFERRALS = [140_000, 120_000, 168_000, 191_000, 202_000]

PR_BY_COUNTRY = {
    "India": 48_213, "China": 27_004, "Philippines": 14_880,
    "United Kingdom": 11_230, "Nepal": 9_455, "Vietnam": 6_102,
    "Pakistan": 5_331, "Sri Lanka": 4_980, "South Africa": 4_410,
    "Iran": 3_902, "Malaysia": 3_640, "Indonesia": 3_120,
    "Thailand": 2_880, "Bangladesh": 2_700, "Brazil": 2_510,
    "Nigeria": 2_300, "Fiji": 2_050, "Ireland": 1_900, "Zimbabwe": 1_740,
}

STUDENTS_BY_COUNTRY = {
    "India": 112_400, "China": 94_200, "Nepal": 38_600, "Philippines": 29_800,
    "Vietnam": 24_100, "Colombia": 19_300, "Pakistan": 16_900, "Brazil": 15_200,
    "Indonesia": 12_700, "Thailand": 11_100, "Sri Lanka": 10_400,
    "Bangladesh": 9_600, "Japan": 8_900, "South Korea": 8_100, "Taiwan": 6_200,
    "Hong Kong": 5_400, "Kenya": 4_900, "Mongolia": 3_200, "Chile": 2_800,
}

PR_BY_STATE_OVER_TIME = {
    "New South Wales": [48_200, 41_600, 66_900, 64_100, 62_400],
    "Victoria": [42_800, 37_100, 59_400, 57_300, 55_900],
    "Queensland": [23_100, 20_400, 33_800, 32_600, 31_200],
    "Western Australia": [16_400, 14_900, 24_100, 23_500, 22_800],
    "South Australia": [9_200, 8_100, 13_400, 13_000, 12_600],
    "Australian Capital Territory": [4_300, 3_800, 6_300, 6_100, 5_900],
    "Tasmania": [2_400, 2_100, 3_400, 3_200, 3_100],
    "Northern Territory": [1_400, 1_200, 2_100, 2_000, 1_900],
}

PR_BY_STATE = {
    "New South Wales": 62_400, "Victoria": 55_900, "Queensland": 31_200,
    "Western Australia": 22_800, "South Australia": 12_600,
    "Australian Capital Territory": 5_900, "Tasmania": 3_100,
    "Northern Territory": 1_900,
}

PR_BY_SUBCLASS = {
    "189 Skilled Independent": 30_400, "190 Skilled Nominated": 24_100,
    "491 Skilled Work Regional": 22_600, "186 Employer Nomination": 21_800,
    "309/100 Partner (offshore)": 20_900, "820/801 Partner (onshore)": 31_700,
    "143 Contributory Parent": 6_800, "132 Business Talent": 1_200,
    "858 Global Talent": 4_000, "187 Regional Sponsored": 3_900,
    "801 Partner (second stage)": 9_400, "804 Aged Parent": 900,
}


def spec(series_id):
    return next(s for s in SPECS if s.id == series_id)


def ranked_records(mapping, year="2024-25", group=""):
    return [(year, label, float(value), group) for label, value in mapping.items()]


def grouped_records(mapping):
    """mapping: {program: (period, {subclass: grants})} -> grouped records."""
    return [
        (period, label, float(value), program)
        for program, (period, rows) in mapping.items()
        for label, value in rows.items()
    ]


def time_records(mapping):
    return [
        (year, label, float(values[i]), label)
        for label, values in mapping.items()
        for i, year in enumerate(YEARS)
    ]


def decorate(series_id, shaped):
    s = spec(series_id)
    shaped.update(
        {
            "id": s.id,
            "title": s.title,
            "subtitle": s.subtitle,
            "unit": s.unit,
            "shape": s.shape,
            "note": s.note,
            "available": True,
            "source_ids": ["sample/not-a-real-file"],
        }
    )
    return shaped


def main() -> int:
    series = {
        "pr_by_stream": decorate(
            "pr_by_stream",
            _shape_time_by_category(spec("pr_by_stream"), time_records(STREAMS)),
        ),
        "pr_by_citizenship": decorate(
            "pr_by_citizenship",
            _shape_ranked(spec("pr_by_citizenship"), ranked_records(PR_BY_COUNTRY)),
        ),
        "pr_by_state": decorate(
            "pr_by_state", _shape_ranked(spec("pr_by_state"), ranked_records(PR_BY_STATE))
        ),
        "pr_by_state_over_time": decorate(
            "pr_by_state_over_time",
            _shape_time_by_category(
                spec("pr_by_state_over_time"), time_records(PR_BY_STATE_OVER_TIME)
            ),
        ),
        "pr_by_subclass": decorate(
            "pr_by_subclass",
            _shape_ranked(spec("pr_by_subclass"), ranked_records(PR_BY_SUBCLASS)),
        ),
        "temp_grants_by_program": decorate(
            "temp_grants_by_program",
            _shape_time_by_category(spec("temp_grants_by_program"), time_records(TEMPORARY)),
        ),
        "student_by_citizenship": decorate(
            "student_by_citizenship",
            _shape_ranked(spec("student_by_citizenship"), ranked_records(STUDENTS_BY_COUNTRY)),
        ),
        "grants_by_subclass": decorate(
            "grants_by_subclass",
            _shape_ranked(spec("grants_by_subclass"), grouped_records(SUBCLASS_GRANTS)),
        ),
        "citizenship_by_prior_country": decorate(
            "citizenship_by_prior_country",
            _shape_ranked(
                spec("citizenship_by_prior_country"),
                ranked_records(CITIZENSHIP_BY_PRIOR_COUNTRY),
            ),
        ),
        "citizenship_conferrals": decorate(
            "citizenship_conferrals",
            _shape_time_total(
                spec("citizenship_conferrals"),
                [(year, "Conferrals", float(CONFERRALS[i]), "") for i, year in enumerate(YEARS)],
            ),
        ),
    }

    # Shown as not-found on purpose: a state-wise citizenship table has not
    # been confirmed as a Home Affairs data file, and the demo should show the
    # honest degradation path rather than promise a card that may never exist.
    missing = spec("citizenship_by_state")
    series[missing.id] = {
        "id": missing.id,
        "title": missing.title,
        "subtitle": missing.subtitle,
        "unit": missing.unit,
        "shape": missing.shape,
        "note": missing.note,
        "available": False,
        "optional": True,
        "reason": (
            "no column matched role 'category'; available columns: "
            "program year, country of prior nationality, conferrals"
        ),
        "source_ids": [],
    }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-09-17T00:00:00+00:00",
        "reporting_window": {"from_program_year": MIN_PROGRAM_YEAR},
        "is_sample": True,
        "run": {
            "status": "ok",
            "series_available": sum(1 for v in series.values() if v.get("available")),
            "series_total": len(series),
            "required_available": sum(1 for v in series.values() if v.get("available")),
            "required_total": sum(1 for v in series.values() if not v.get("optional")),
            "problems": [
                {
                    "series": missing.id,
                    "reason": series[missing.id]["reason"],
                    "optional": True,
                }
            ],
        },
        "series": series,
        "headline": build_headline(series),
        "sources": [
            {
                "id": "sample/not-a-real-file",
                "title": "SAMPLE DATA — not a real Home Affairs file",
                "dataset_url": "https://data.gov.au/data/organization/immi",
                "resource_name": "Invented figures for layout preview only",
                "format": "n/a",
                "row_count": 0,
                "sheet": None,
                "dataset_updated": None,
                "resource_updated": None,
            }
        ],
    }

    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(series)} series, {len(payload['headline'])} tiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
