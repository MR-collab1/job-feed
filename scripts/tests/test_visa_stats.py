"""Offline tests for the visa-statistics pipeline.

These exercise the parts that break when Home Affairs changes a file: header
detection under a preamble, column-name drift, suppressed counts, total rows,
and the shaping that turns rows into chart payloads. Network access is never
required - the fixtures stand in for downloaded resources.

    python3 -m unittest discover -s scripts/tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visa_stats import tables  # noqa: E402
from visa_stats.build import (  # noqa: E402
    _extract_rows, _shape_ranked, _shape_time_by_category, build_headline,
)
from visa_stats.catalogue import CatalogueError  # noqa: E402
from visa_stats.columns import (  # noqa: E402
    ColumnError, Role, period_sort_key, period_start_year, resolve_roles,
)
from visa_stats.sources import (  # noqa: E402
    COUNTRY, GRANTS, MIN_PROGRAM_YEAR, PERIOD, SPECS, STREAM, Part, SeriesSpec,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> tables.Table:
    return tables.read_csv((FIXTURES / name).read_bytes())


class HeaderDetectionTests(unittest.TestCase):
    def test_skips_title_and_blank_preamble(self):
        table = load_fixture("pr_by_citizenship.csv")
        self.assertEqual(
            table.headers, ["program year", "country of citizenship", "visas granted"]
        )
        self.assertTrue(table.notes, "preamble rows should be retained as notes")

    def test_detects_semicolon_delimiter(self):
        table = load_fixture("student_grants.csv")
        self.assertEqual(table.headers, ["financial year", "visa grants"])
        self.assertEqual(len(table), 3)

    def test_rows_are_dicts_keyed_by_normalised_header(self):
        table = load_fixture("pr_by_stream.csv")
        self.assertEqual(table.rows[0]["stream"], "Skilled")
        self.assertEqual(table.rows[0]["outcome"], "79,620")


class HeaderNormalisationTests(unittest.TestCase):
    def test_collapses_whitespace_case_and_footnote_markers(self):
        for raw in ("Country of\nCitizenship ", "COUNTRY OF CITIZENSHIP", "Country of Citizenship[1]"):
            self.assertEqual(tables.normalise_header(raw), "country of citizenship")

    def test_duplicate_headers_are_made_unique(self):
        grid = [["Year", "Grants", "Grants"], ["2024-25", "1", "2"]]
        table = tables._table_from_grid(grid, sheet=None)
        self.assertEqual(table.headers, ["year", "grants", "grants 2"])
        self.assertEqual(table.rows[0]["grants 2"], "2")


class NumberParsingTests(unittest.TestCase):
    def test_parses_thousands_separators_and_percentages(self):
        self.assertEqual(tables.parse_number("48,213"), 48213.0)
        self.assertEqual(tables.parse_number("1 234"), 1234.0)
        self.assertEqual(tables.parse_number("45.6%"), 45.6)
        self.assertEqual(tables.parse_number("(120)"), -120.0)

    def test_suppressed_and_missing_counts_are_unknown_not_zero(self):
        # "<5" means "withheld for privacy" - reading it as 5 would invent data.
        for token in ("<5", "", "n/a", "np", "-", ".."):
            self.assertIsNone(tables.parse_number(token), token)


class ColumnResolutionTests(unittest.TestCase):
    def test_resolves_roles_across_naming_variants(self):
        table = load_fixture("pr_by_citizenship.csv")
        roles = resolve_roles(table, (PERIOD, COUNTRY, GRANTS))
        self.assertEqual(roles["period"], "program year")
        self.assertEqual(roles["category"], "country of citizenship")
        self.assertEqual(roles["value"], "visas granted")

    def test_each_role_claims_a_distinct_column(self):
        table = load_fixture("pr_by_stream.csv")
        roles = resolve_roles(table, (PERIOD, STREAM, GRANTS))
        self.assertEqual(len(set(roles.values())), 3)

    def test_missing_required_role_raises_with_available_columns(self):
        table = load_fixture("student_grants.csv")
        with self.assertRaises(ColumnError) as caught:
            resolve_roles(table, (PERIOD, COUNTRY, GRANTS))
        self.assertIn("visa grants", str(caught.exception))

    def test_optional_role_is_simply_absent(self):
        table = load_fixture("student_grants.csv")
        roles = resolve_roles(
            table, (PERIOD, Role("category", COUNTRY.patterns, required=False), GRANTS)
        )
        self.assertNotIn("category", roles)
        self.assertEqual(roles["value"], "visa grants")

    def test_falls_back_to_the_only_numeric_column(self):
        grid = [["Financial year", "Places delivered"], ["2024-25", "1,000"]]
        table = tables._table_from_grid(grid, sheet=None)
        roles = resolve_roles(table, (PERIOD, GRANTS))
        self.assertEqual(roles["value"], "places delivered")


class PeriodOrderingTests(unittest.TestCase):
    def test_program_years_sort_chronologically_despite_en_dashes(self):
        labels = ["2023–24", "2021-22", "2022/23"]
        self.assertEqual(
            sorted(labels, key=period_sort_key), ["2021-22", "2022/23", "2023–24"]
        )

    def test_month_labels_sort_within_a_year(self):
        labels = ["Dec 2024", "Jul 2024", "Jan 2025"]
        self.assertEqual(
            sorted(labels, key=period_sort_key), ["Jul 2024", "Dec 2024", "Jan 2025"]
        )

    def test_unparseable_labels_sort_last_rather_than_vanish(self):
        self.assertEqual(
            sorted(["zzz unknown", "2024-25"], key=period_sort_key),
            ["2024-25", "zzz unknown"],
        )


def _records(table, period_col, category_col, value_col, group=""):
    return [
        (row[period_col], row[category_col], tables.parse_number(row[value_col]), group)
        for row in table.rows
        if tables.parse_number(row[value_col]) is not None
    ]


class RankedShapeTests(unittest.TestCase):
    def setUp(self):
        table = load_fixture("pr_by_citizenship.csv")
        raw = _records(table, "program year", "country of citizenship", "visas granted")
        # Aggregate rows are dropped upstream in _extract_rows; mirror that here.
        self.records = [r for r in raw if r[1] not in {"Total", "Not stated"}]
        self.spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="ranked", unit="places", parts=(), top_n=3
        )

    def test_uses_only_the_latest_period(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertEqual(shaped["period"], "2024-25")
        self.assertEqual(shaped["periods"], ["2023-24", "2024-25"])

    def test_top_n_then_folds_the_tail(self):
        shaped = _shape_ranked(self.spec, self.records)
        labels = [i["label"] for i in shaped["items"]]
        self.assertEqual(labels[:3], ["India", "China", "Philippines"])
        self.assertEqual(labels[-1], "All other")
        self.assertTrue(shaped["items"][-1]["is_tail"])

    def test_tail_preserves_the_total(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertAlmostEqual(
            sum(i["value"] for i in shaped["items"]), shaped["total"], places=6
        )

    def test_shares_sum_to_one(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertAlmostEqual(sum(i["share"] for i in shaped["items"]), 1.0, places=3)


class TimeByCategoryShapeTests(unittest.TestCase):
    def setUp(self):
        table = load_fixture("pr_by_stream.csv")
        raw = _records(table, "program year", "stream", "outcome")
        self.records = [r for r in raw if r[1] != "Grand total"]
        self.spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="time_by_category",
            unit="places", parts=(), top_n=7,
        )

    def test_matrix_is_categories_by_periods_in_chronological_order(self):
        shaped = _shape_time_by_category(self.spec, self.records)
        self.assertEqual(shaped["periods"], ["2021–22", "2022–23", "2023–24"])
        self.assertEqual(len(shaped["matrix"]), len(shaped["categories"]))
        self.assertTrue(all(len(row) == 3 for row in shaped["matrix"]))

    def test_categories_are_ordered_by_total_size(self):
        shaped = _shape_time_by_category(self.spec, self.records)
        self.assertEqual(shaped["categories"][0], "Skilled")

    def test_missing_period_category_pairs_become_zero_not_gaps(self):
        records = [("2023-24", "Skilled", 10.0, ""), ("2024-25", "Family", 5.0, "")]
        shaped = _shape_time_by_category(self.spec, records)
        self.assertEqual(sorted(shaped["categories"]), ["Family", "Skilled"])
        for row in shaped["matrix"]:
            self.assertEqual(len(row), 2)
            self.assertIn(0.0, row)

    def test_categories_past_the_cap_fold_into_other(self):
        records = [("2024-25", f"Cat{i}", float(20 - i), "") for i in range(12)]
        shaped = _shape_time_by_category(self.spec, records)
        self.assertEqual(len(shaped["categories"]), 8)
        self.assertEqual(shaped["categories"][-1], "Other")
        self.assertAlmostEqual(
            sum(row[0] for row in shaped["matrix"]),
            sum(v for _, _, v, _g in records),
            places=6,
        )


class FullListTests(unittest.TestCase):
    """The chart shows a top-N; the table needs every category."""

    def setUp(self):
        self.spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="ranked", unit="x",
            parts=(), top_n=3,
        )
        self.records = [("2024-25", f"C{i}", float(100 - i), "") for i in range(10)]

    def test_the_chart_list_is_capped_but_the_full_list_is_not(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertEqual(len(shaped["items"]), 4)          # 3 + folded tail
        self.assertEqual(len(shaped["all_items"]), 10)

    def test_ranks_are_dense_and_start_at_one(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertEqual([r["rank"] for r in shaped["all_items"]], list(range(1, 11)))

    def test_the_full_list_is_ordered_by_value(self):
        values = [r["value"] for r in _shape_ranked(self.spec, self.records)["all_items"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_full_list_shares_sum_to_one(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertAlmostEqual(sum(r["share"] for r in shaped["all_items"]), 1.0, places=3)

    def test_the_full_list_carries_no_synthetic_tail_row(self):
        labels = [r["label"] for r in _shape_ranked(self.spec, self.records)["all_items"]]
        self.assertNotIn("All other", labels)

    def test_an_unbounded_category_count_is_capped(self):
        from visa_stats.build import FULL_LIST_LIMIT
        records = [("2024-25", f"C{i}", float(1000 - i), "") for i in range(FULL_LIST_LIMIT + 40)]
        shaped = _shape_ranked(self.spec, records)
        self.assertEqual(len(shaped["all_items"]), FULL_LIST_LIMIT)
        # The count still reports the truth, even though the list is trimmed.
        self.assertEqual(shaped["category_count"], FULL_LIST_LIMIT + 40)

    def test_the_full_list_carries_the_program_group(self):
        records = [
            ("2024-25", "600 Visitor", 30.0, "Visitor"),
            ("2023-24", "500 Student", 10.0, "Student"),
        ]
        rows = _shape_ranked(self.spec, records)["all_items"]
        self.assertEqual(rows[0]["group"], "Visitor")
        self.assertEqual(rows[1]["period"], "2023-24")


class GroupedRankedTests(unittest.TestCase):
    """A ranked series combining programs on different publishing cycles."""

    def setUp(self):
        self.spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="ranked", unit="grants",
            parts=(), top_n=10,
        )
        # Visitor has published 2024-25; Student's latest release is 2023-24.
        self.records = [
            ("2024-25", "600 Visitor", 3_000.0, "Visitor"),
            ("2023-24", "600 Visitor", 2_000.0, "Visitor"),
            ("2023-24", "500 Student", 900.0, "Student"),
            ("2022-23", "500 Student", 800.0, "Student"),
        ]

    def test_each_group_contributes_its_own_latest_period(self):
        shaped = _shape_ranked(self.spec, self.records)
        values = {i["label"]: i["value"] for i in shaped["items"]}
        # A single global latest period would have dropped Student entirely.
        self.assertEqual(values["600 Visitor"], 3_000.0)
        self.assertEqual(values["500 Student"], 900.0)

    def test_reference_periods_are_reported_per_group(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertEqual(
            shaped["reference_periods"], {"Visitor": "2024-25", "Student": "2023-24"}
        )

    def test_period_label_spans_the_range_when_groups_disagree(self):
        shaped = _shape_ranked(self.spec, self.records)
        self.assertEqual(shaped["period"], "2023-24 to 2024-25")

    def test_period_label_is_a_single_year_when_groups_agree(self):
        records = [
            ("2024-25", "600 Visitor", 3_000.0, "Visitor"),
            ("2024-25", "500 Student", 900.0, "Student"),
        ]
        self.assertEqual(_shape_ranked(self.spec, records)["period"], "2024-25")

    def test_each_item_names_the_program_it_came_from(self):
        shaped = _shape_ranked(self.spec, self.records)
        groups = {i["label"]: i.get("group") for i in shaped["items"]}
        self.assertEqual(groups["600 Visitor"], "Visitor")
        self.assertEqual(groups["500 Student"], "Student")

    def test_a_category_split_across_groups_is_attributed_to_the_larger(self):
        records = [
            ("2024-25", "485 Graduate", 100.0, "Temporary graduate"),
            ("2024-25", "485 Graduate", 900.0, "Student"),
        ]
        item = _shape_ranked(self.spec, records)["items"][0]
        self.assertEqual(item["value"], 1_000.0)
        self.assertEqual(item["group"], "Student")

    def test_single_source_series_report_no_groups(self):
        records = [("2024-25", "India", 10.0, ""), ("2024-25", "China", 5.0, "")]
        shaped = _shape_ranked(self.spec, records)
        self.assertEqual(shaped["reference_periods"], {})
        self.assertNotIn("group", shaped["items"][0])


class HeadlineTests(unittest.TestCase):
    def test_computes_total_and_year_on_year_change(self):
        series = {
            "pr_by_stream": {
                "available": True,
                "periods": ["2023-24", "2024-25"],
                "matrix": [[100.0, 150.0], [50.0, 50.0]],
                "source_ids": ["a"],
            }
        }
        tile = build_headline(series)[0]
        self.assertEqual(tile["value"], 200.0)
        self.assertEqual(tile["period"], "2024-25")
        self.assertEqual(tile["change_pct"], 33.3)

    def test_unavailable_series_produce_no_tiles(self):
        self.assertEqual(build_headline({"pr_by_stream": {"available": False}}), [])

    def test_top_country_tile_skips_the_folded_tail(self):
        series = {
            "pr_by_citizenship": {
                "available": True,
                "period": "2024-25",
                "items": [
                    {"label": "India", "value": 10.0, "share": 0.5},
                    {"label": "All other", "value": 10.0, "share": 0.5, "is_tail": True},
                ],
                "source_ids": [],
            }
        }
        tile = build_headline(series)[0]
        self.assertEqual(tile["caption"], "India")


class FailureHandlingTests(unittest.TestCase):
    def test_a_failed_run_keeps_previously_published_series(self):
        from build_visa_stats import _preserve_on_total_failure

        previous = {
            "series": {"pr_by_stream": {"available": True, "periods": ["2024-25"]}},
            "generated_at": "2026-09-01T00:00:00+00:00",
        }
        failed = {"run": {"status": "failed", "problems": []}, "generated_at": "2026-09-17T00:00:00+00:00"}

        merged = _preserve_on_total_failure(failed, previous)
        self.assertTrue(merged["series"]["pr_by_stream"]["available"])
        self.assertEqual(merged["generated_at"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(merged["last_checked_at"], "2026-09-17T00:00:00+00:00")
        self.assertTrue(merged["run"]["retained_previous"])

    def test_a_partial_run_is_written_through_unchanged(self):
        from build_visa_stats import _preserve_on_total_failure

        payload = {"run": {"status": "partial"}, "series": {}, "generated_at": "x"}
        self.assertIs(_preserve_on_total_failure(payload, {"series": {}}), payload)


class ReportingWindowTests(unittest.TestCase):
    """The dashboard reports from MIN_PROGRAM_YEAR onward."""

    def setUp(self):
        self.spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="time_by_category",
            unit="places", parts=(),
        )
        self.part = Part(
            dataset_slugs=("x",), search_terms="x", resource_patterns=(r"x",),
            roles=(PERIOD, STREAM, GRANTS),
        )
        self.roles = {"period": "program year", "category": "stream", "value": "grants"}

    def _rows(self, periods):
        table = tables.Table(
            headers=["program year", "stream", "grants"],
            rows=[{"program year": p, "stream": "Skilled", "grants": "10"} for p in periods],
        )
        return _extract_rows(self.spec, self.part, table, self.roles)

    def test_period_start_year_reads_the_common_label_shapes(self):
        self.assertEqual(period_start_year("2023-24"), 2023)
        self.assertEqual(period_start_year("2023\u201324"), 2023)
        self.assertEqual(period_start_year("2024/2025"), 2024)
        self.assertEqual(period_start_year("Jul 2024"), 2024)
        self.assertIsNone(period_start_year("not a period"))

    def test_years_before_the_window_are_dropped(self):
        kept = [r[0] for r in self._rows(["2017-18", "2019-20", "2020-21", "2024-25"])]
        self.assertEqual(kept, ["2020-21", "2024-25"])

    def test_the_first_year_in_the_window_is_kept(self):
        kept = [r[0] for r in self._rows([f"{MIN_PROGRAM_YEAR}-{MIN_PROGRAM_YEAR % 100 + 1}"])]
        self.assertEqual(len(kept), 1)

    def test_an_unreadable_period_is_dropped_rather_than_assumed_recent(self):
        # Keeping it would let pre-window data through under an odd label.
        self.assertEqual(self._rows(["mystery period"]), [])

    def test_a_series_with_no_period_column_is_untouched(self):
        part = Part(
            dataset_slugs=("x",), search_terms="x", resource_patterns=(r"x",),
            roles=(STREAM, GRANTS), label="Student",
        )
        table = tables.Table(
            headers=["stream", "grants"], rows=[{"stream": "Skilled", "grants": "10"}]
        )
        rows = _extract_rows(self.spec, part, table, {"category": "stream", "value": "grants"})
        self.assertEqual(len(rows), 1)


class CategoryCapTests(unittest.TestCase):
    def test_the_cap_comes_from_the_spec(self):
        spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="time_by_category",
            unit="x", parts=(), max_categories=3,
        )
        records = [("2024-25", f"C{i}", float(10 - i), "") for i in range(6)]
        shaped = _shape_time_by_category(spec, records)
        self.assertEqual(len(shaped["categories"]), 4)
        self.assertEqual(shaped["categories"][-1], "Other")

    def test_all_eight_states_survive_a_cap_of_eight(self):
        spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="time_by_category",
            unit="x", parts=(), max_categories=8,
        )
        records = [("2024-25", f"S{i}", float(10 - i), "") for i in range(8)]
        shaped = _shape_time_by_category(spec, records)
        self.assertEqual(len(shaped["categories"]), 8)
        self.assertNotIn("Other", shaped["categories"])

    def test_a_cap_above_eight_is_clamped_to_the_palette(self):
        # A ninth categorical hue is not distinguishable under CVD.
        spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="time_by_category",
            unit="x", parts=(), max_categories=20,
        )
        records = [("2024-25", f"C{i}", float(30 - i), "") for i in range(15)]
        shaped = _shape_time_by_category(spec, records)
        self.assertEqual(len(shaped["categories"]), 9)  # 8 + Other

    def test_the_folded_tail_preserves_the_total(self):
        spec = SeriesSpec(
            id="t", title="t", subtitle="", shape="time_by_category",
            unit="x", parts=(), max_categories=3,
        )
        records = [("2024-25", f"C{i}", float(10 - i), "") for i in range(6)]
        shaped = _shape_time_by_category(spec, records)
        self.assertAlmostEqual(
            sum(row[0] for row in shaped["matrix"]),
            sum(v for _, _, v, _g in records),
            places=6,
        )


class OptionalSeriesTests(unittest.TestCase):
    """A series we are not sure Home Affairs publishes must fail quietly."""

    def setUp(self):
        self.required = SeriesSpec(
            id="req", title="Required", subtitle="", shape="ranked",
            unit="x", parts=(), optional=False,
        )
        self.other = SeriesSpec(
            id="req2", title="Also required", subtitle="", shape="ranked",
            unit="x", parts=(), optional=False,
        )
        self.optional = SeriesSpec(
            id="opt", title="Optional", subtitle="", shape="ranked",
            unit="x", parts=(), optional=True,
        )

    def _build(self, specs, failing):
        """Run build() with the network replaced by a canned outcome."""
        from visa_stats import build as build_module

        def fake_build_series(spec):
            if spec.id in failing:
                raise CatalogueError(f"no dataset found for {spec.id}")
            return ({"available": True, "items": [], "source_ids": []}, [])

        with mock.patch.object(build_module, "build_series", fake_build_series):
            return build_module.build(specs)

    def test_an_optional_failure_leaves_the_run_ok(self):
        payload = self._build(
            (self.required, self.other, self.optional), failing={"opt"}
        )
        self.assertEqual(payload["run"]["status"], "ok")
        self.assertEqual(payload["run"]["required_available"], 2)
        self.assertEqual(payload["run"]["required_total"], 2)

    def test_a_required_failure_still_degrades_the_run(self):
        payload = self._build(
            (self.required, self.other, self.optional), failing={"req"}
        )
        self.assertEqual(payload["run"]["status"], "partial")

    def test_every_required_series_failing_is_a_failed_run(self):
        payload = self._build(
            (self.required, self.other, self.optional), failing={"req", "req2", "opt"}
        )
        self.assertEqual(payload["run"]["status"], "failed")

    def test_the_failure_is_still_reported_with_its_reason(self):
        payload = self._build(
            (self.required, self.other, self.optional), failing={"opt"}
        )
        problem = next(p for p in payload["run"]["problems"] if p["series"] == "opt")
        self.assertTrue(problem["optional"])
        self.assertIn("no dataset found", problem["reason"])

    def test_the_series_is_marked_optional_so_the_page_can_skip_it(self):
        payload = self._build(
            (self.required, self.other, self.optional), failing={"opt"}
        )
        self.assertTrue(payload["series"]["opt"]["optional"])
        self.assertFalse(payload["series"]["opt"]["available"])

    def test_a_required_failure_is_not_marked_optional(self):
        payload = self._build(
            (self.required, self.other, self.optional), failing={"req"}
        )
        self.assertFalse(payload["series"]["req"]["optional"])


class SpecIntegrityTests(unittest.TestCase):
    """The specs are pure data, so guard them the way data gets guarded."""

    def test_ids_are_unique(self):
        ids = [spec.id for spec in SPECS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_spec_has_parts_a_known_shape_and_a_value_role(self):
        for spec in SPECS:
            with self.subTest(spec=spec.id):
                self.assertTrue(spec.parts, "spec has no source parts")
                self.assertIn(spec.shape, {"ranked", "time_by_category", "time_total"})
                for part in spec.parts:
                    roles = {role.name for role in part.roles}
                    self.assertIn("value", roles)
                    self.assertTrue(part.dataset_slugs)
                    self.assertTrue(part.resource_patterns)

    def test_role_and_drop_patterns_compile(self):
        for spec in SPECS:
            for pattern in spec.drop_rows:
                re.compile(pattern)
            for part in spec.parts:
                for role in part.roles:
                    for pattern in role.patterns:
                        with self.subTest(spec=spec.id, role=role.name, pattern=pattern):
                            re.compile(pattern)

    def test_a_multi_part_spec_labels_every_part(self):
        # Parts without a category column rely on their label for the series name.
        for spec in SPECS:
            if len(spec.parts) < 2:
                continue
            for part in spec.parts:
                with self.subTest(spec=spec.id):
                    has_category = any(r.name == "category" for r in part.roles)
                    self.assertTrue(part.label or has_category)

    def test_the_page_knows_how_to_draw_every_spec(self):
        """A new spec with no card config would render but never be ordered."""
        app = (Path(__file__).resolve().parents[2] / "visa" / "assets" / "app.js").read_text()
        display = set(re.findall(r"^\s{4}(\w+):\s*\{ chart:", app, re.MULTILINE))
        order = set(re.findall(r"'(\w+)'", app[app.index("var ORDER"):app.index("];", app.index("var ORDER"))]))

        for spec in SPECS:
            with self.subTest(spec=spec.id):
                self.assertIn(spec.id, display, "missing from DISPLAY in app.js")
                self.assertIn(spec.id, order, "missing from ORDER in app.js")


if __name__ == "__main__":
    unittest.main()
