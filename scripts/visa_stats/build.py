"""Assemble every spec in ``sources`` into the JSON the dashboard reads.

A failure in one series never sinks the run: the series is recorded as
unavailable with its reason, the rest still publish, and the page shows an
honest gap rather than a stale or invented number.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict

from .catalogue import CatalogueError, Dataset, Resource, find_dataset, load_table, pick_resource
from .columns import ColumnError, period_sort_key, resolve_roles, tidy_category, tidy_period
from .http import FetchError
from .sources import SPECS, Part, SeriesSpec
from .tables import Table, TableError, parse_number

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

# (period, category, value, group) - group names the program that published the
# row, and is empty for single-source series.
Record = tuple[str, str, float, str]


def build(specs: tuple[SeriesSpec, ...] = SPECS) -> dict:
    """Run every spec and return the full dashboard payload."""
    series: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    problems: list[dict] = []

    for spec in specs:
        log.info("building series %s", spec.id)
        try:
            built, used = build_series(spec)
        except (FetchError, CatalogueError, ColumnError, TableError) as exc:
            log.warning("series %s unavailable: %s", spec.id, exc)
            problems.append(
                {"series": spec.id, "reason": str(exc), "optional": spec.optional}
            )
            series[spec.id] = _unavailable(spec, str(exc))
            continue

        series[spec.id] = built
        for source in used:
            sources[source["id"]] = source

    available = [s for s in series.values() if s.get("available")]
    required_total = sum(1 for spec in specs if not spec.optional)
    required_available = sum(
        1 for spec in specs
        if not spec.optional and series[spec.id].get("available")
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "run": {
            "status": _status(required_available, required_total),
            "series_available": len(available),
            "series_total": len(specs),
            "required_available": required_available,
            "required_total": required_total,
            "problems": problems,
        },
        "series": series,
        "sources": sorted(sources.values(), key=lambda s: s["title"]),
    }
    payload["headline"] = build_headline(series)
    return payload


def build_series(spec: SeriesSpec) -> tuple[dict, list[dict]]:
    """Load every part of a spec and fold it into one chartable series."""
    records: list[Record] = []  # (period, category, value, group)
    sources: list[dict] = []

    for part in spec.parts:
        rows, source = _load_part(spec, part)
        records.extend(rows)
        sources.append(source)

    if not records:
        raise CatalogueError("source tables produced no usable rows")

    if spec.shape == "ranked":
        built = _shape_ranked(spec, records)
    elif spec.shape == "time_total":
        built = _shape_time_total(spec, records)
    else:
        built = _shape_time_by_category(spec, records)

    built.update(
        {
            "id": spec.id,
            "title": spec.title,
            "subtitle": spec.subtitle,
            "unit": spec.unit,
            "shape": spec.shape,
            "note": spec.note,
            "available": True,
            "source_ids": [s["id"] for s in sources],
        }
    )
    return built, sources


def _load_part(spec: SeriesSpec, part: Part) -> tuple[list[Record], dict]:
    dataset = find_dataset(part.dataset_slugs, part.search_terms)
    resource = pick_resource(dataset, part.resource_patterns)
    table = load_table(resource, sheet_pattern=part.sheet_pattern)
    roles = resolve_roles(table, part.roles)

    rows = _extract_rows(spec, part, table, roles)
    log.info(
        "%s: %d rows from %s / %s", spec.id, len(rows), dataset.slug, resource.name or resource.id
    )
    return rows, _source_record(dataset, resource, table)


def _extract_rows(
    spec: SeriesSpec, part: Part, table: Table, roles: dict[str, str]
) -> list[Record]:
    """Pull (period, category, value, group) records out of a resolved table."""
    period_col = roles.get("period")
    category_col = roles.get("category")
    value_col = roles["value"]

    drops = tuple(spec.drop_rows)
    group = part.label or ""
    output: list[Record] = []

    for row in table.rows:
        if not _passes_filters(row, part.row_filters):
            continue

        value = parse_number(row.get(value_col))
        if value is None:
            continue

        period = tidy_period(row.get(period_col, "")) if period_col else ""
        if category_col:
            category = tidy_category(row.get(category_col, ""))
        else:
            category = part.label or spec.title

        if not category or _is_aggregate(category, drops):
            continue
        if period_col and (not period or _is_aggregate(period, drops)):
            continue

        output.append((period, category, value, group))

    return output


def _passes_filters(row: dict[str, str], filters: dict[str, str]) -> bool:
    import re

    return all(
        re.search(pattern, row.get(column, ""), re.IGNORECASE)
        for column, pattern in filters.items()
    )


def _is_aggregate(label: str, drops: tuple[str, ...]) -> bool:
    import re

    return any(re.search(pattern, label, re.IGNORECASE) for pattern in drops)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

def _shape_time_by_category(spec: SeriesSpec, records) -> dict:
    """periods x categories matrix, for stacked bars and multi-line charts."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for period, category, value, _group in records:
        totals[(period, category)] += value

    periods = sorted({p for p, _ in totals}, key=period_sort_key)
    ranked = sorted(
        {c for _, c in totals},
        key=lambda c: -sum(v for (_, cat), v in totals.items() if cat == c),
    )
    categories = _cap_categories(ranked, spec.top_n if spec.top_n < 8 else 7)

    matrix = [
        [_fold(totals, period, category, ranked, categories) for period in periods]
        for category in categories
    ]
    return {"periods": periods, "categories": categories, "matrix": matrix}


def _shape_time_total(spec: SeriesSpec, records) -> dict:
    """One value per period."""
    totals: dict[str, float] = defaultdict(float)
    for period, _category, value, _group in records:
        totals[period] += value

    periods = sorted(totals, key=period_sort_key)
    return {
        "periods": periods,
        "categories": [spec.title],
        "matrix": [[totals[p] for p in periods]],
    }


def _shape_ranked(spec: SeriesSpec, records: list[Record]) -> dict:
    """Top-N categories for the latest period, with a folded tail.

    "Latest" is resolved *per group*, because a series can combine programs on
    different release cycles. Taking a single global latest period would drop
    every program that has not published that year yet, which would look like a
    collapse in the data rather than a difference in publishing schedules.
    """
    periods = sorted({p for p, _, _, _ in records if p}, key=period_sort_key)
    latest_by_group = _latest_period_per_group(records)

    totals: dict[str, float] = defaultdict(float)
    group_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for period, category, value, group in records:
        reference = latest_by_group.get(group, "")
        if reference and period != reference:
            continue
        totals[category] += value
        group_totals[category][group] += value

    ranked = sorted(totals.items(), key=lambda item: -item[1])
    head = ranked[: spec.top_n]
    tail = ranked[spec.top_n:]

    items: list[dict] = []
    for label, value in head:
        item = {"label": label, "value": value}
        group = _dominant_group(group_totals[label])
        if group:
            item["group"] = group
            item["period"] = latest_by_group.get(group, "")
        items.append(item)

    if tail:
        items.append({"label": "All other", "value": sum(v for _, v in tail), "is_tail": True})

    total = sum(v for _, v in ranked)
    for item in items:
        item["share"] = round(item["value"] / total, 4) if total else 0.0

    reference_periods = {g: p for g, p in latest_by_group.items() if g and p}

    return {
        "period": _reference_label(latest_by_group),
        "periods": periods,
        "items": items,
        "total": total,
        "category_count": len(ranked),
        "reference_periods": reference_periods,
    }


def _latest_period_per_group(records: list[Record]) -> dict[str, str]:
    """The most recent period each group actually published."""
    seen: dict[str, list[str]] = defaultdict(list)
    for period, _category, _value, group in records:
        if period:
            seen[group].append(period)
    return {
        group: sorted(found, key=period_sort_key)[-1]
        for group, found in seen.items()
        if found
    }


def _dominant_group(totals: dict[str, float]) -> str:
    """The group contributing most of a category's value."""
    named = {g: v for g, v in totals.items() if g}
    return max(named, key=named.get) if named else ""


def _reference_label(latest_by_group: dict[str, str]) -> str:
    """One period label when every group agrees, otherwise a range."""
    found = sorted({p for p in latest_by_group.values() if p}, key=period_sort_key)
    if not found:
        return ""
    if len(found) == 1:
        return found[0]
    return f"{found[0]} to {found[-1]}"


def _cap_categories(ranked: list[str], limit: int) -> list[str]:
    """Keep the largest categories; anything past the cap folds into 'Other'."""
    if len(ranked) <= limit:
        return ranked
    return ranked[:limit] + ["Other"]


def _fold(totals, period, category, ranked, kept) -> float:
    if category != "Other" or "Other" in ranked:
        return round(totals.get((period, category), 0.0), 2)
    folded = [c for c in ranked if c not in kept]
    return round(sum(totals.get((period, c), 0.0) for c in folded), 2)


# ---------------------------------------------------------------------------
# Headline tiles
# ---------------------------------------------------------------------------

def build_headline(series: dict[str, dict]) -> list[dict]:
    """Derive the stat tiles at the top of the page from the built series."""
    tiles: list[dict] = []

    stream = series.get("pr_by_stream", {})
    if stream.get("available") and stream.get("periods"):
        latest_index = len(stream["periods"]) - 1
        latest_total = sum(row[latest_index] for row in stream["matrix"])
        tile = {
            "id": "pr_total",
            "label": "Permanent places delivered",
            "value": latest_total,
            "unit": "places",
            "period": stream["periods"][latest_index],
            "source_ids": stream.get("source_ids", []),
        }
        if latest_index > 0:
            previous = sum(row[latest_index - 1] for row in stream["matrix"])
            if previous:
                tile["change_pct"] = round((latest_total - previous) / previous * 100, 1)
                tile["compare_period"] = stream["periods"][latest_index - 1]
        tiles.append(tile)

    country = series.get("pr_by_citizenship", {})
    if country.get("available") and country.get("items"):
        top = next((i for i in country["items"] if not i.get("is_tail")), None)
        if top:
            tiles.append(
                {
                    "id": "pr_top_country",
                    "label": "Largest source country",
                    "value": top["value"],
                    "unit": "places",
                    "caption": top["label"],
                    "share": top["share"],
                    "period": country.get("period", ""),
                    "source_ids": country.get("source_ids", []),
                }
            )

    temporary = series.get("temp_grants_by_program", {})
    if temporary.get("available") and temporary.get("periods"):
        last = len(temporary["periods"]) - 1
        tiles.append(
            {
                "id": "temp_total",
                "label": "Temporary visa grants",
                "value": sum(row[last] for row in temporary["matrix"]),
                "unit": "grants",
                "period": temporary["periods"][last],
                "caption": "Student, visitor and skilled temporary programs",
                "source_ids": temporary.get("source_ids", []),
            }
        )

    conferrals = series.get("citizenship_conferrals", {})
    if conferrals.get("available") and conferrals.get("periods"):
        last = len(conferrals["periods"]) - 1
        tiles.append(
            {
                "id": "conferrals",
                "label": "Citizenship conferrals",
                "value": conferrals["matrix"][0][last],
                "unit": "people",
                "period": conferrals["periods"][last],
                "source_ids": conferrals.get("source_ids", []),
            }
        )

    return tiles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_record(dataset: Dataset, resource: Resource, table: Table) -> dict:
    return {
        "id": f"{dataset.slug}/{resource.id or resource.name}",
        "title": dataset.title,
        "dataset_url": dataset.url,
        "resource_name": resource.name,
        "resource_url": resource.url,
        "format": resource.fmt,
        "sheet": table.sheet,
        "row_count": len(table),
        "dataset_updated": dataset.last_modified,
        "resource_updated": resource.last_modified,
    }


def _unavailable(spec: SeriesSpec, reason: str) -> dict:
    return {
        "id": spec.id,
        "title": spec.title,
        "subtitle": spec.subtitle,
        "unit": spec.unit,
        "shape": spec.shape,
        "note": spec.note,
        "available": False,
        "optional": spec.optional,
        "reason": reason,
        "source_ids": [],
    }


def _status(available: int, total: int) -> str:
    if available == 0:
        return "failed"
    return "ok" if available == total else "partial"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
