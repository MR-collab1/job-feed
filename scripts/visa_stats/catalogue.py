"""CKAN client for data.gov.au, plus dataset/resource resolution.

Resource IDs on data.gov.au change whenever an agency republishes a file, so
nothing here hardcodes one. Datasets are looked up by their stable slug (with a
keyword search as a backstop) and the resource is chosen by matching its name.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass

from .http import FetchError, fetch
from .tables import Table, read_resource, table_from_records

log = logging.getLogger(__name__)

CKAN_BASE = "https://data.gov.au/data/api/3/action"
DATASET_PAGE = "https://data.gov.au/data/dataset"

# Home Affairs publishes under the "immi" organisation slug on data.gov.au.
HOME_AFFAIRS_ORG = "immi"

# Formats we can actually parse, best first.
FORMAT_PREFERENCE = ["csv", "xlsx", "xlsm", "xls", "tsv"]


class CatalogueError(RuntimeError):
    """Raised when a dataset or a usable resource could not be found."""


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    fmt: str
    url: str
    last_modified: str | None
    datastore_active: bool


@dataclass(frozen=True)
class Dataset:
    slug: str
    title: str
    last_modified: str | None
    resources: tuple[Resource, ...]

    @property
    def url(self) -> str:
        return f"{DATASET_PAGE}/{self.slug}"


def ckan_action(action: str, **params) -> dict:
    """Call a CKAN action endpoint and return its ``result`` payload."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{CKAN_BASE}/{action}?{query}"
    payload = json.loads(fetch(url).text)
    if not payload.get("success"):
        raise CatalogueError(f"CKAN action {action} failed: {payload.get('error')}")
    return payload["result"]


def get_dataset(slug: str) -> Dataset:
    """Fetch one dataset by slug."""
    return _to_dataset(ckan_action("package_show", id=slug))


def find_dataset(slugs: tuple[str, ...], search_terms: str) -> Dataset:
    """Resolve the first slug that exists, falling back to a keyword search.

    The fallback keeps the pipeline alive when Home Affairs renames a dataset:
    we lose the exact match but still find the replacement under the same org.
    """
    problems: list[str] = []

    for slug in slugs:
        try:
            return get_dataset(slug)
        except (FetchError, CatalogueError) as exc:
            problems.append(f"{slug}: {exc}")

    log.warning("no candidate slug resolved (%s); searching instead", "; ".join(problems))
    result = ckan_action(
        "package_search",
        q=search_terms,
        fq=f"organization:{HOME_AFFAIRS_ORG}",
        rows=5,
    )
    matches = result.get("results") or []
    if not matches:
        raise CatalogueError(
            f"no dataset found for slugs {slugs} or search {search_terms!r}"
        )
    log.info("search fallback matched %s", matches[0].get("name"))
    return _to_dataset(matches[0])


def pick_resource(dataset: Dataset, name_patterns: tuple[str, ...]) -> Resource:
    """Choose the resource whose name best matches, preferring parseable formats."""
    usable = [r for r in dataset.resources if _format_rank(r.fmt) is not None]
    if not usable:
        raise CatalogueError(
            f"dataset {dataset.slug} has no CSV/XLSX resources "
            f"(formats: {sorted({r.fmt for r in dataset.resources})})"
        )

    scored: list[tuple[tuple[int, int, int], Resource]] = []
    for resource in usable:
        haystack = f"{resource.name} {resource.url}".lower()
        rank = next(
            (
                index
                for index, pattern in enumerate(name_patterns)
                if re.search(pattern, haystack, re.IGNORECASE)
            ),
            len(name_patterns),
        )
        scored.append(((rank, _format_rank(resource.fmt), len(resource.name)), resource))

    scored.sort(key=lambda item: item[0])
    best_rank = scored[0][0][0]
    if best_rank == len(name_patterns):
        log.warning(
            "no resource in %s matched %s; using %r",
            dataset.slug, name_patterns, scored[0][1].name,
        )
    return scored[0][1]


def load_table(resource: Resource, *, sheet_pattern: str | None = None) -> Table:
    """Load a resource as a Table, via the datastore when one is available.

    The datastore is preferred because it returns typed JSON records and skips
    the spreadsheet-preamble guesswork entirely.
    """
    if resource.datastore_active:
        try:
            return _load_from_datastore(resource)
        except (FetchError, CatalogueError) as exc:
            log.warning("datastore read failed for %s (%s); downloading file", resource.id, exc)

    body = fetch(resource.url).body
    fmt = resource.fmt or _format_from_url(resource.url)
    return read_resource(body, fmt=fmt, sheet_pattern=sheet_pattern)


def _load_from_datastore(resource: Resource, page_size: int = 10_000) -> Table:
    records: list[dict] = []
    fields: list[str] | None = None
    offset = 0

    while True:
        result = ckan_action(
            "datastore_search",
            resource_id=resource.id,
            limit=page_size,
            offset=offset,
        )
        if fields is None:
            fields = [
                f["id"] for f in result.get("fields", []) if f.get("id") != "_id"
            ]
        batch = result.get("records") or []
        records.extend(batch)
        offset += len(batch)
        if len(batch) < page_size or offset >= result.get("total", 0):
            break
        if offset > 250_000:  # guard against a runaway table
            log.warning("stopping datastore paging for %s at %d rows", resource.id, offset)
            break

    if not records:
        raise CatalogueError(f"datastore returned no records for {resource.id}")
    return table_from_records(records, fields)


def _to_dataset(payload: dict) -> Dataset:
    resources = tuple(
        Resource(
            id=r.get("id", ""),
            name=(r.get("name") or "").strip(),
            fmt=(r.get("format") or "").strip(),
            url=r.get("url") or "",
            last_modified=r.get("last_modified") or r.get("created"),
            datastore_active=bool(r.get("datastore_active")),
        )
        for r in payload.get("resources", [])
        if r.get("url")
    )
    return Dataset(
        slug=payload.get("name", ""),
        title=(payload.get("title") or payload.get("name") or "").strip(),
        last_modified=payload.get("metadata_modified"),
        resources=resources,
    )


def _format_rank(fmt: str) -> int | None:
    kind = (fmt or "").strip().lower().lstrip(".")
    return FORMAT_PREFERENCE.index(kind) if kind in FORMAT_PREFERENCE else None


def _format_from_url(url: str) -> str:
    return urllib.parse.urlparse(url).path.rsplit(".", 1)[-1].lower()
