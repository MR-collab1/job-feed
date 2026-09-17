"""Declarative specs for every series the dashboard draws.

Each spec says *where* the numbers live (candidate dataset slugs, a resource
name pattern) and *what the columns mean* (role patterns), never which exact
file or column. Adding a chart means adding a spec here - no new parsing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .columns import Role

# ---------------------------------------------------------------------------
# Reusable role definitions
# ---------------------------------------------------------------------------

PERIOD = Role(
    name="period",
    patterns=(
        r"^(program|financial|migration)?\s*year$",
        r"(program|financial|migration)\s*year",
        r"year of (grant|arrival|settlement|conferral)",
        r"^(fy|year|period)$",
        r"year",
    ),
)

COUNTRY = Role(
    name="category",
    patterns=(
        r"^country of citizenship$",
        r"citizenship country",
        r"country of citizenship",
        r"^citizenship$",
        r"^country$",
        r"nationality",
        r"country",
    ),
)

STREAM = Role(
    name="category",
    patterns=(
        r"^(migration )?stream$",
        r"^visa stream$",
        r"^(program )?category$",
        r"stream",
        r"category",
    ),
)

STATE = Role(
    name="category",
    patterns=(
        r"state\s*/?\s*territory",
        r"^state$",
        r"intended (state|location)",
        r"location",
    ),
)

SUBCLASS = Role(
    name="category",
    patterns=(
        r"visa subclass",
        r"^subclass$",
        r"subclass",
        r"visa (type|name)",
    ),
)

GRANTS = Role(
    name="value",
    patterns=(
        r"^(visas? )?granted$",
        r"^(number of )?(visa )?grants$",
        r"^outcome$",
        r"^places?$",
        r"^(total|count|number|value)$",
        r"grant",
        r"outcome",
        r"count",
    ),
    numeric=True,
)

CONFERRALS = Role(
    name="value",
    patterns=(
        r"conferral",
        r"^(number of )?(people|persons|clients)$",
        r"^(total|count|number|value)$",
        r"count",
    ),
    numeric=True,
)

# Rows that are totals or placeholders rather than real categories.
AGGREGATE_ROWS = (
    r"^total\b",
    r"^all\b",
    r"^grand total",
    r"^sub[- ]?total",
    r"^not (stated|recorded|specified|applicable)",
    r"^unknown\b",
    r"^other\b",
    r"^n/?a$",
)


@dataclass(frozen=True)
class Part:
    """One source table feeding a series.

    ``label`` names the program this table covers. For a series with no
    category column it becomes the category; for a subclass breakdown it is
    carried through as the *group* a subclass belongs to, so a chart can say
    which program subclass 500 sits in without spending a colour on it.
    """

    dataset_slugs: tuple[str, ...]
    search_terms: str
    resource_patterns: tuple[str, ...]
    roles: tuple[Role, ...]
    sheet_pattern: str | None = None
    label: str | None = None
    row_filters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SeriesSpec:
    """A chartable series assembled from one or more source tables."""

    id: str
    title: str
    subtitle: str
    shape: str  # "time_by_category" | "ranked" | "time_total"
    unit: str
    parts: tuple[Part, ...]
    top_n: int = 15
    drop_rows: tuple[str, ...] = AGGREGATE_ROWS
    note: str = ""


PERMANENT_PROGRAM_SLUGS = (
    "permanent-migration-program-skilled-family",
    "australian-migration-statistics",
)

PERMANENT_PROGRAM_SEARCH = "permanent migration program skilled family outcomes"

VISITOR_SLUGS = ("visitor-visas-granted-pivot-table", "visitor-visas")

CITIZENSHIP_SLUGS = (
    "australian-migration-statistics",
    "historical-migration-statistics",
    "citizenship-statistics",
)

# Every program that publishes a subclass-level grant breakdown. Partner visas
# have no standalone dataset - 309/820/100/801 are reported inside the
# permanent migration program, which is why that program is in this list.
SUBCLASS_PROGRAMS = (
    ("Student", ("student-visas",), "student visa grants by subclass"),
    ("Visitor", VISITOR_SLUGS, "visitor visa grants by subclass"),
    (
        "Temporary work (skilled)",
        ("visa-temporary-work-skilled",),
        "temporary work skilled visa grants subclass 457 482",
    ),
    (
        "Temporary graduate",
        ("temporary-graduate-visas",),
        "temporary graduate visa grants subclass 485",
    ),
    (
        "Working holiday maker",
        ("visa-working-holiday-maker",),
        "working holiday maker visa grants subclass 417 462",
    ),
    (
        "Permanent migration",
        PERMANENT_PROGRAM_SLUGS,
        "permanent migration program visa subclass partner skilled",
    ),
)


def _subclass_part(label, slugs, search_terms) -> "Part":
    """A subclass breakdown for one visa program."""
    return Part(
        label=label,
        dataset_slugs=slugs,
        search_terms=search_terms,
        resource_patterns=(r"subclass", r"grant", r"program"),
        sheet_pattern=r"subclass|grant",
        roles=(
            SUBCLASS,
            GRANTS,
            Role("period", PERIOD.patterns, required=False),
        ),
    )


SPECS: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        id="pr_by_stream",
        title="Permanent Migration Program outcomes by stream",
        subtitle="Places delivered each program year, split by migration stream",
        shape="time_by_category",
        unit="places",
        parts=(
            Part(
                dataset_slugs=("historical-migration-statistics",) + PERMANENT_PROGRAM_SLUGS,
                search_terms="migration program outcomes by stream program year",
                resource_patterns=(
                    r"stream",
                    r"migration program",
                    r"outcome",
                ),
                sheet_pattern=r"stream|program|outcome",
                roles=(PERIOD, STREAM, GRANTS),
            ),
        ),
        note="Program years run 1 July to 30 June.",
    ),
    SeriesSpec(
        id="pr_by_citizenship",
        title="Permanent Migration Program by country of citizenship",
        subtitle="Places delivered in the most recent program year on record",
        shape="ranked",
        unit="places",
        parts=(
            Part(
                dataset_slugs=PERMANENT_PROGRAM_SLUGS,
                search_terms=PERMANENT_PROGRAM_SEARCH + " country of citizenship",
                resource_patterns=(
                    r"citizenship",
                    r"country",
                    r"nationality",
                ),
                sheet_pattern=r"citizenship|country",
                roles=(COUNTRY, GRANTS, Role("period", PERIOD.patterns, required=False)),
            ),
        ),
        top_n=15,
    ),
    SeriesSpec(
        id="pr_by_state",
        title="Permanent Migration Program by state and territory",
        subtitle="Where permanent places were taken up, most recent program year",
        shape="ranked",
        unit="places",
        parts=(
            Part(
                dataset_slugs=PERMANENT_PROGRAM_SLUGS,
                search_terms=PERMANENT_PROGRAM_SEARCH + " state territory",
                resource_patterns=(r"state", r"territory", r"location"),
                sheet_pattern=r"state|territory|location",
                roles=(STATE, GRANTS, Role("period", PERIOD.patterns, required=False)),
            ),
        ),
        top_n=10,
    ),
    SeriesSpec(
        id="pr_by_subclass",
        title="Permanent Migration Program by visa subclass",
        subtitle="Places delivered by subclass, most recent program year",
        shape="ranked",
        unit="places",
        parts=(
            Part(
                dataset_slugs=PERMANENT_PROGRAM_SLUGS,
                search_terms=PERMANENT_PROGRAM_SEARCH + " visa subclass",
                resource_patterns=(r"subclass", r"visa type"),
                sheet_pattern=r"subclass|visa",
                roles=(SUBCLASS, GRANTS, Role("period", PERIOD.patterns, required=False)),
            ),
        ),
        top_n=12,
    ),
    SeriesSpec(
        id="temp_grants_by_program",
        title="Temporary visa grants by program",
        subtitle="Grants per program year across the major temporary visa programs",
        shape="time_by_category",
        unit="grants",
        parts=(
            Part(
                label="Student",
                dataset_slugs=("student-visas",),
                search_terms="student visa program grants",
                resource_patterns=(r"grant", r"program", r"student"),
                sheet_pattern=r"grant|program",
                roles=(PERIOD, GRANTS),
            ),
            Part(
                label="Visitor",
                dataset_slugs=VISITOR_SLUGS,
                search_terms="visitor visa program grants",
                resource_patterns=(r"grant", r"program", r"visitor"),
                sheet_pattern=r"grant|program",
                roles=(PERIOD, GRANTS),
            ),
            Part(
                label="Temporary work (skilled)",
                dataset_slugs=("visa-temporary-work-skilled",),
                search_terms="temporary work skilled visa program grants",
                resource_patterns=(r"grant", r"program", r"skilled"),
                sheet_pattern=r"grant|program",
                roles=(PERIOD, GRANTS),
            ),
            Part(
                label="Temporary graduate",
                dataset_slugs=("temporary-graduate-visas",),
                search_terms="temporary graduate visa program grants subclass 485",
                resource_patterns=(r"grant", r"program", r"graduate"),
                sheet_pattern=r"grant|program",
                roles=(PERIOD, GRANTS),
            ),
            Part(
                label="Working holiday maker",
                dataset_slugs=("visa-working-holiday-maker",),
                search_terms="working holiday maker visa program grants",
                resource_patterns=(r"grant", r"program", r"holiday"),
                sheet_pattern=r"grant|program",
                roles=(PERIOD, GRANTS),
            ),
        ),
        note="Each program is published separately; grants are summed per program year.",
    ),
    SeriesSpec(
        id="student_by_citizenship",
        title="Student visa grants by country of citizenship",
        subtitle="Most recent program year on record",
        shape="ranked",
        unit="grants",
        parts=(
            Part(
                dataset_slugs=("student-visas",),
                search_terms="student visa grants country of citizenship",
                resource_patterns=(r"citizenship", r"country", r"nationality"),
                sheet_pattern=r"citizenship|country",
                roles=(COUNTRY, GRANTS, Role("period", PERIOD.patterns, required=False)),
            ),
        ),
        top_n=15,
    ),
    SeriesSpec(
        id="grants_by_subclass",
        title="Visa grants by subclass",
        subtitle="Across every program that publishes a subclass breakdown",
        shape="ranked",
        unit="grants",
        parts=tuple(_subclass_part(*program) for program in SUBCLASS_PROGRAMS),
        # Deliberately generous: the program filter can only narrow to what the
        # published top-N retained, so a tight cap would hide small programs.
        top_n=25,
        note=(
            "Subclasses are counted in the most recent program year each source "
            "publishes, so programs on different release cycles may not share a "
            "reference period. Partner subclasses are reported inside the "
            "permanent migration program."
        ),
    ),
    SeriesSpec(
        id="citizenship_conferrals",
        title="Australian citizenship conferrals",
        subtitle="People who became Australian citizens by conferral, per program year",
        shape="time_total",
        unit="people",
        parts=(
            Part(
                dataset_slugs=CITIZENSHIP_SLUGS,
                search_terms="australian citizenship conferrals by year",
                resource_patterns=(r"conferral", r"citizenship"),
                sheet_pattern=r"conferral|citizenship",
                roles=(PERIOD, CONFERRALS),
            ),
        ),
    ),
    SeriesSpec(
        id="citizenship_by_prior_country",
        title="Citizenship conferrals by country of prior nationality",
        subtitle="Where new Australian citizens held citizenship before conferral",
        shape="ranked",
        unit="people",
        parts=(
            Part(
                dataset_slugs=CITIZENSHIP_SLUGS,
                search_terms="citizenship conferrals country of prior nationality",
                resource_patterns=(r"conferral", r"prior nationality", r"citizenship"),
                sheet_pattern=r"conferral|nationality|citizenship",
                roles=(
                    Role(
                        name="category",
                        patterns=(
                            r"^country of (prior|former) (nationality|citizenship)$",
                            r"(prior|former) (nationality|citizenship)",
                            r"country of birth",
                        ) + COUNTRY.patterns,
                    ),
                    CONFERRALS,
                    Role("period", PERIOD.patterns, required=False),
                ),
            ),
        ),
        top_n=15,
    ),
)
