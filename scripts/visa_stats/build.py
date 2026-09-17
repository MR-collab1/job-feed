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

SCHEMA_VERSION = 2


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
            problems.append({"series": spec.id, "reason": str(exc)})
            series[spec.id] = _unavailable(spec, str(exc))
            continue

        series[spec.id] = built
        for source in used:
            sources[source["id"]] = source

    available = [s for s in series.values() if s.get("available")]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "run": {
            "status": _status(len(available), len(specs)),
            "series_available": len(available),
            "series_total": len(specs),
            "problems": problems,
        },
        "series": series,
        "sources": sorted(sources.values(), key=lambda s: s["title"]),
    }
    payload["headline"] = build_headline(series)
    return payload


def build_series(spec: SeriesSpec) -> tuple[dict, list[dict]]:
    """Load every part of a spec and fold it into one chartable series."""
    records: list[tuple[str, str, float]] = []  # (period, category, value)
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


def _load_part(spec: SeriesSpec, part: Part) -> tuple[list[tuple[str, str, float]], dict]:
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
) -> list[tuple[str, str, float]]:
    """Pull (period, category, value) triples out of a resolved table."""
    period_col = roles.get("period")
    category_col = roles.get("category")
    value_col = roles["value"]

    drops = tuple(spec.drop_rows)
    output: list[tuple[str, str, float]] = []

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

        output.append((period, category, value))

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
    for period, category, value in records:
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
    for period, _, value in records:
        totals[period] += value

    periods = sorted(totals, key=period_sort_key)
    return {
        "periods": periods,
        "categories": [spec.title],
        "matrix": [[totals[p] for p in periods]],
    }


def _shape_ranked(spec: SeriesSpec, records) -> dict:
    """Top-N categories for the latest period present, with a folded tail."""
    periods = sorted({p for p, _, _ in records if p}, key=period_sort_key)
    latest = periods[-1] if periods else ""

    totals: dict[str, float] = defaultdict(float)
    for period, category, value in records:
        if latest and period != latest:
            continue
        totals[category] += value

    ranked = sorted(totals.items(), key=lambda item: -item[1])
    head = ranked[: spec.top_n]
    tail = ranked[spec.top_n:]

    items = [{"label": label, "value": value} for label, value in head]
    if tail:
        items.append({"label": "All other", "value": sum(v for _, v in tail), "is_tail": True})

    total = sum(v for _, v in ranked)
    for item in items:
        item["share"] = round(item["value"] / total, 4) if total else 0.0

    return {
        "period": latest,
        "periods": periods,
        "items": items,
        "total": total,
        "category_count": len(ranked),
    }


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
        "reason": reason,
        "source_ids": [],
    }


def _status(available: int, total: int) -> str:
    if available == 0:
        return "failed"
    return "ok" if available == total else "partial"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
