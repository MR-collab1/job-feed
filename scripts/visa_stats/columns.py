"""Map real-world spreadsheet columns onto the roles a chart needs.

Home Affairs renames columns between releases ("Citizenship country", "Country
of citizenship", "Citizenship Country of Applicant"). Rather than pin exact
names, each series declares an ordered list of patterns per role and the best
match wins, so a rename degrades into a warning instead of a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tables import Table, parse_number

# Financial/program year: 2023-24, 2023–24, 2023/2024, FY2023-24
_FY = re.compile(r"(?:fy\s*)?(\d{4})\s*[-–/]\s*(\d{2,4})")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_MONTHS = {
    name: index
    for index, name in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}


class ColumnError(RuntimeError):
    """Raised when a required column role has no match in the table."""


@dataclass(frozen=True)
class Role:
    """One column a series needs, described by patterns instead of a name."""

    name: str
    patterns: tuple[str, ...]
    required: bool = True
    numeric: bool = False


def resolve_roles(table: Table, roles: tuple[Role, ...]) -> dict[str, str]:
    """Return ``{role_name: header}``, raising if a required role is unmatched.

    Earlier patterns beat later ones, and an exact match beats a substring
    match, so a table carrying both "grants" and "grants total" resolves
    predictably.
    """
    resolved: dict[str, str] = {}
    taken: set[str] = set()

    for role in roles:
        header = _best_header(table, role, taken)
        if header is None and role.numeric:
            header = _only_numeric_column(table, taken)
        if header is None:
            if role.required:
                raise ColumnError(
                    f"no column matched role {role.name!r}; "
                    f"available columns: {', '.join(table.headers) or '(none)'}"
                )
            continue
        resolved[role.name] = header
        taken.add(header)

    return resolved


def _best_header(table: Table, role: Role, taken: set[str]) -> str | None:
    candidates = [h for h in table.headers if h and h not in taken]
    for pattern in role.patterns:
        matcher = re.compile(pattern, re.IGNORECASE)
        exact = [h for h in candidates if matcher.fullmatch(h)]
        if exact:
            return exact[0]
        partial = [h for h in candidates if matcher.search(h)]
        if partial:
            # Prefer the shortest match: "grants" over "grants by state total".
            return min(partial, key=len)
    return None


def _only_numeric_column(table: Table, taken: set[str]) -> str | None:
    """Fall back to the single column that parses as numbers throughout."""
    sample = table.rows[:50]
    if not sample:
        return None

    numeric = []
    for header in table.headers:
        if not header or header in taken:
            continue
        values = [parse_number(row.get(header)) for row in sample]
        filled = [v for v in values if v is not None]
        if filled and len(filled) >= len(sample) * 0.6:
            numeric.append(header)

    return numeric[0] if len(numeric) == 1 else None


def period_sort_key(label: str) -> tuple:
    """Order period labels chronologically without needing real dates.

    Handles program years ("2023-24"), calendar years ("2024"), and
    month labels ("Jul 2024", "2024-07"). Unrecognised labels sort last,
    alphabetically, so they stay visible rather than silently reordering.
    """
    text = (label or "").strip().lower()

    fy = _FY.search(text)
    if fy:
        start = int(fy.group(1))
        return (0, start, 0, text)

    iso = re.match(r"^(\d{4})[-/](\d{1,2})$", text)
    if iso:
        return (0, int(iso.group(1)), int(iso.group(2)), text)

    month = re.search(r"\b([a-z]{3})[a-z]*\b", text)
    year = _YEAR.search(text)
    if month and year and month.group(1) in _MONTHS:
        return (0, int(year.group(0)), _MONTHS[month.group(1)], text)

    if year and re.fullmatch(r"\D*\d{4}\D*", text):
        return (0, int(year.group(0)), 0, text)

    return (1, 0, 0, text)


def tidy_period(label: str) -> str:
    """Render a period label consistently (en dashes to hyphens, trimmed)."""
    return re.sub(r"\s+", " ", (label or "").replace("–", "-")).strip()


def tidy_category(label: str) -> str:
    """Title-case a category label unless it is already mixed case."""
    text = re.sub(r"\s+", " ", (label or "")).strip()
    if text and text.isupper() and len(text) > 3:
        return text.title()
    return text
