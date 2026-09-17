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
from visa_stats.sources import SPECS  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "visa" / "data" / "sample.json"

YEARS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

STREAMS = {
    "Skilled": [95_800, 79_600, 79_600, 142_900, 132_200, 124_100],
    "Family": [41_900, 77_400, 52_500, 52_500, 52_500, 55_300],
    "Special Eligibility": [200, 100, 100, 110, 300, 400],
}

TEMPORARY = {
    "Visitor": [2_100_000, 90_000, 240_000, 2_600_000, 3_100_000, 3_250_000],
    "Student": [383_000, 175_000, 188_000, 577_000, 527_000, 489_000],
    "Temporary work (skilled)": [48_000, 22_000, 31_000, 80_000, 74_000, 68_000],
}

CONFERRALS = [204_000, 140_000, 120_000, 168_000, 191_000, 202_000]

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


def ranked_records(mapping, year="2024-25"):
    return [(year, label, float(value)) for label, value in mapping.items()]


def time_records(mapping):
    return [
        (year, label, float(values[i]))
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
        "citizenship_conferrals": decorate(
            "citizenship_conferrals",
            _shape_time_total(
                spec("citizenship_conferrals"),
                [(year, "Conferrals", float(CONFERRALS[i])) for i, year in enumerate(YEARS)],
            ),
        ),
    }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-09-17T00:00:00+00:00",
        "is_sample": True,
        "run": {
            "status": "ok",
            "series_available": len(series),
            "series_total": len(series),
            "problems": [],
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
