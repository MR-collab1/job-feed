"""Load and merge the two audit workbooks into one attendance-level dataset.

The audit covers A&E attendances by patients presenting with alcohol
intoxication or withdrawal.  Two workbooks describe the same cohort:

``Audit_CIWA.xlsx`` (sheet ``Feuille 1``)
    The master attendance list for the whole audit window.  Its clinical
    columns are filled in for the earlier attendances and left empty for the
    later ones, which had not been case-noted when the sheet was circulated.

``AE_PresentingComplaint_39_2.xlsx`` (sheet ``Sheet3`` only)
    The second round of case-note review.  It repeats the same 19 columns and
    fills in exactly the attendances the master list left blank.

Both sheets key on ``LOCAL PATIENT ID`` plus ``AE ARRIVAL DATE TIME``.  A patient
can attend more than once, so the patient ID alone is not unique — several
patients in this cohort re-attend within days.  Merging on the pair keeps each
attendance distinct.  Where both sheets describe the same attendance, the
case-note review wins, because it is the completed audit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import load_workbook

from . import normalise as nz

#: The 19 audit columns, in source order, mapped to the field names used
#: everywhere downstream.  Header text differs slightly between the two
#: workbooks (one has a non-breaking space and a "(Y/N)" suffix), so columns are
#: matched by position after a header sanity check rather than by exact text.
COLUMNS: Sequence[tuple[str, str]] = (
    ("LOCAL PATIENT ID", "patient_id"),
    ("AGE ON ATTENDANCE", "age"),
    ("AE ARRIVAL DATE TIME", "arrival"),
    ("AE DEPARTURE DATE TIME", "departure"),
    ("LAST LOCATION NAME", "last_location"),
    ("PRESENTING COMPLAINT", "presenting_complaint"),
    ("AE DISPOSAL REASON", "disposal_reason"),
    ("Was CIWA score documented?", "ciwa_documented_raw"),
    ("What was initial CIWA score?", "ciwa_score_raw"),
    ("At what time was CIWA scoring done?", "ciwa_time_raw"),
    ("Which treatment was given?", "treatment_raw"),
    ("What time was the treatment given?", "treatment_time_raw"),
    ("Pabrnex /thiamine given?", "thiamine_raw"),
    ("IV fluids given?", "iv_fluids_raw"),
    ("Benzodiazepine given?", "benzodiazepine_raw"),
    ("CIWA score repeated?", "ciwa_repeated_raw"),
    ("Number of repeat scores?", "repeat_count_raw"),
    ("Medical Referral made? (Y/N)", "medical_referral_raw"),
    ("Admitted/discharged?", "outcome_raw"),
)

#: Fields supplied by the case-note review sheet.  Everything before these comes
#: from the patient administration extract and is identical in both workbooks.
CLINICAL_FIELDS: Sequence[str] = tuple(name for _, name in COLUMNS[7:])

AUDIT_SHEET = "Feuille 1"
COMPLAINT_SHEET = "Sheet3"

_DATE_FORMATS = ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_datetime(value: object) -> dt.datetime | None:
    """Parse a cell that may already be a datetime or may be ``dd/mm/yyyy hh:mm`` text.

    The master workbook stores real datetimes; the case-note sheet stores
    UK-format strings.  Day-first is assumed for the text form, which is
    unambiguous here because the audit window spans days past the 12th.
    """
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    text = nz.clean(value)
    if not text or nz.is_blank(text):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Attendance:
    """One A&E attendance, after normalisation."""

    patient_id: str
    age: int | None
    arrival: dt.datetime | None
    departure: dt.datetime | None
    last_location: str
    presenting_complaint: str
    disposal_code: str | None
    disposal_label: str
    # Clinical audit fields, normalised.
    ciwa_documentation: str
    ciwa_score: int | None
    ciwa_band: str | None
    ciwa_scoring_time_recorded: bool
    ciwa_repeated: str
    repeat_count: int | None
    thiamine_given: str
    iv_fluids_given: str
    benzodiazepine_given: str
    drugs_given: tuple[str, ...]
    doses_mg: tuple[float, ...]
    treatment_time_recorded: bool
    medical_referral: str
    outcome: str
    # Provenance.
    source: str
    clinical_source: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def pseudonym(self) -> str:
        """A stable, non-reversible stand-in for the local patient ID."""
        digest = hashlib.sha256(self.patient_id.upper().encode()).hexdigest()
        return f"P-{digest[:10]}"

    @property
    def length_of_stay_hours(self) -> float | None:
        """Hours between arrival and departure, or ``None`` if either is missing."""
        if self.arrival is None or self.departure is None:
            return None
        hours = (self.departure - self.arrival).total_seconds() / 3600
        return round(hours, 2) if hours >= 0 else None

    @property
    def month(self) -> str | None:
        """Arrival month as ``YYYY-MM``."""
        return self.arrival.strftime("%Y-%m") if self.arrival else None

    @property
    def benzodiazepine_treated(self) -> bool:
        """True when a benzodiazepine was given, by either column or drug name."""
        return self.benzodiazepine_given == nz.YES or bool(
            set(self.drugs_given) & nz.BENZODIAZEPINES
        )

    @property
    def thiamine_treated(self) -> bool:
        """True when Pabrinex/thiamine was given, by either column or drug name."""
        return self.thiamine_given == nz.YES or bool(set(self.drugs_given) & nz.THIAMINE_DRUGS)

    @property
    def iv_fluids_treated(self) -> bool:
        return self.iv_fluids_given == nz.YES

    @property
    def pharmacologically_treated(self) -> bool:
        """The audit's headline definition of "treated".

        A benzodiazepine or thiamine replacement, i.e. the drugs that actually
        treat alcohol withdrawal.  IV fluids alone are supportive care and do
        not count here; they are reported separately.
        """
        return self.benzodiazepine_treated or self.thiamine_treated

    @property
    def any_treatment(self) -> bool:
        """Any intervention at all, including IV fluids alone."""
        return self.pharmacologically_treated or self.iv_fluids_treated

    @property
    def ciwa_scored(self) -> bool:
        return self.ciwa_documentation == nz.DOCUMENTED or self.ciwa_score is not None

    @property
    def ciwa_repeat_performed(self) -> bool:
        """True only where a repeat score was positively recorded."""
        return self.ciwa_repeated == nz.YES or (self.repeat_count or 0) > 0


def _looks_like_header(row: Sequence[Any]) -> bool:
    first = nz.clean(row[0]).lower()
    return first == "local patient id"


def _is_empty_row(values: Sequence[Any]) -> bool:
    return all(nz.clean(v) == "" for v in values)


def _is_section_divider(values: Sequence[Any]) -> bool:
    """True for the banner rows the auditors typed into the patient ID column.

    The master workbook contains a row reading "1ST NOVEMBER 2025 TO 27TH
    DECEMBER 2025" as a visual separator.  It is not a patient.
    """
    patient_id = nz.clean(values[0])
    if not patient_id:
        return False
    if parse_datetime(values[2]) is not None:
        return False
    # A real local patient ID is alphanumeric with no spaces, e.g. RLT1649140.
    return bool(re.search(r"\s", patient_id)) or not re.match(r"^[A-Za-z]+\d", patient_id)


def _read_sheet(path: Path, sheet: str) -> list[dict[str, Any]]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        if sheet not in workbook.sheetnames:
            raise KeyError(f"{path.name} has no sheet named {sheet!r}; found {workbook.sheetnames}")
        worksheet = workbook[sheet]
        rows = list(worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()

    if not rows or not _looks_like_header(rows[0]):
        raise ValueError(f"{path.name}!{sheet} does not start with the expected audit header")

    width = len(COLUMNS)
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        values = list(row)[:width]
        values += [None] * (width - len(values))
        if _is_empty_row(values) or _is_section_divider(values):
            continue
        records.append({name: values[index] for index, (_, name) in enumerate(COLUMNS)})
    return records


def _merge_key(record: dict[str, Any]) -> tuple[str, dt.datetime | None]:
    return nz.clean(record["patient_id"]).upper(), parse_datetime(record["arrival"])


def _has_clinical_content(record: dict[str, Any]) -> bool:
    return any(not nz.is_blank(record.get(name)) for name in CLINICAL_FIELDS)


def _to_attendance(record: dict[str, Any], source: str, clinical_source: str) -> Attendance:
    age_text = nz.clean(record["age"])
    try:
        age = int(round(float(age_text))) if age_text and not nz.is_blank(age_text) else None
    except ValueError:
        age = None

    code, label = nz.disposal(record["disposal_reason"])
    score = nz.ciwa_score(record["ciwa_score_raw"])
    drugs = nz.find_drugs(record["treatment_raw"])
    # The dedicated benzodiazepine column sometimes names the drug instead of
    # answering yes/no, so fold those names in too.
    drugs = sorted(set(drugs) | set(nz.find_drugs(record["benzodiazepine_raw"])))

    return Attendance(
        patient_id=nz.clean(record["patient_id"]).upper(),
        age=age,
        arrival=parse_datetime(record["arrival"]),
        departure=parse_datetime(record["departure"]),
        last_location=nz.clean(record["last_location"]) or "Not recorded",
        presenting_complaint=nz.clean(record["presenting_complaint"]),
        disposal_code=code,
        disposal_label=label,
        ciwa_documentation=nz.ciwa_documentation(record["ciwa_documented_raw"]),
        ciwa_score=score,
        ciwa_band=nz.ciwa_band(score),
        ciwa_scoring_time_recorded=not nz.is_blank(record["ciwa_time_raw"]),
        ciwa_repeated=nz.tri_state(record["ciwa_repeated_raw"]),
        repeat_count=nz.repeat_count(record["repeat_count_raw"]),
        thiamine_given=nz.tri_state(record["thiamine_raw"]),
        iv_fluids_given=nz.tri_state(record["iv_fluids_raw"]),
        benzodiazepine_given=nz.tri_state(record["benzodiazepine_raw"]),
        drugs_given=tuple(drugs),
        doses_mg=tuple(nz.find_doses_mg(record["treatment_raw"])),
        treatment_time_recorded=not nz.is_blank(record["treatment_time_raw"]),
        medical_referral=nz.tri_state(record["medical_referral_raw"]),
        outcome=nz.outcome(record["outcome_raw"]),
        source=source,
        clinical_source=clinical_source,
        raw=record,
    )


@dataclass(frozen=True)
class MergeReport:
    """What the merge actually did, so the numbers can be audited."""

    audit_rows: int
    complaint_rows: int
    merged_rows: int
    matched_attendances: int
    complaint_only_attendances: int
    clinical_fields_filled_from_complaint: int
    attendances_without_clinical_review: int


@dataclass(frozen=True)
class Dataset:
    attendances: tuple[Attendance, ...]
    merge_report: MergeReport

    def __len__(self) -> int:
        return len(self.attendances)

    def __iter__(self):
        return iter(self.attendances)

    def filtered(
        self,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        outcome: str | None = None,
        ciwa_documented: bool | None = None,
    ) -> tuple[Attendance, ...]:
        """Return attendances matching every supplied filter.

        ``date_from`` and ``date_to`` are inclusive and compare against the
        arrival date.  Attendances with no arrival date are dropped whenever a
        date filter is in play, because they cannot be placed in the window.
        """
        rows = self.attendances
        if date_from is not None:
            rows = tuple(a for a in rows if a.arrival and a.arrival.date() >= date_from)
        if date_to is not None:
            rows = tuple(a for a in rows if a.arrival and a.arrival.date() <= date_to)
        if outcome is not None:
            rows = tuple(a for a in rows if a.outcome == outcome)
        if ciwa_documented is not None:
            rows = tuple(a for a in rows if a.ciwa_scored is ciwa_documented)
        return rows


def merge_records(
    audit_records: Iterable[dict[str, Any]],
    complaint_records: Iterable[dict[str, Any]],
) -> Dataset:
    """Merge the master attendance list with the case-note review sheet."""
    audit_list = list(audit_records)
    complaint_list = list(complaint_records)
    complaint_by_key = {_merge_key(r): r for r in complaint_list}

    merged: list[Attendance] = []
    matched = 0
    filled = 0
    used_keys: set[tuple[str, dt.datetime | None]] = set()

    for record in audit_list:
        key = _merge_key(record)
        review = complaint_by_key.get(key)
        clinical_source = "audit_ciwa"
        if review is not None:
            used_keys.add(key)
            matched += 1
            # The case-note review is the completed audit, so it wins wherever
            # it recorded something.  Blank review cells fall back to the
            # master sheet rather than wiping a value that was already there.
            for name in CLINICAL_FIELDS:
                if not nz.is_blank(review.get(name)):
                    record[name] = review[name]
                    filled += 1
            if _has_clinical_content(review):
                clinical_source = "presenting_complaint_sheet3"
        merged.append(_to_attendance(record, source="audit_ciwa", clinical_source=clinical_source))

    # Any attendance the review found that the master list never had.
    complaint_only = 0
    for key, record in complaint_by_key.items():
        if key in used_keys:
            continue
        complaint_only += 1
        merged.append(
            _to_attendance(
                record,
                source="presenting_complaint_sheet3",
                clinical_source="presenting_complaint_sheet3",
            )
        )

    merged.sort(key=lambda a: (a.arrival or dt.datetime.max, a.patient_id))
    unreviewed = sum(1 for a in merged if not _has_clinical_content(a.raw))

    report = MergeReport(
        audit_rows=len(audit_list),
        complaint_rows=len(complaint_list),
        merged_rows=len(merged),
        matched_attendances=matched,
        complaint_only_attendances=complaint_only,
        clinical_fields_filled_from_complaint=filled,
        attendances_without_clinical_review=unreviewed,
    )
    return Dataset(attendances=tuple(merged), merge_report=report)


def default_data_dir() -> Path:
    """Where the source workbooks live.

    Override with ``ALCOHOL_AUDIT_DATA_DIR``.  The workbooks hold identifiable
    patient records, so they are deliberately not committed to the repository.
    """
    configured = os.environ.get("ALCOHOL_AUDIT_DATA_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data"


def _resolve(data_dir: Path, env_var: str, pattern: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    matches = sorted(data_dir.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"No file matching {pattern!r} in {data_dir}. "
            f"Set {env_var} or ALCOHOL_AUDIT_DATA_DIR to point at the source workbooks."
        )
    return matches[0]


def load_dataset(data_dir: Path | str | None = None) -> Dataset:
    """Load both workbooks from ``data_dir`` and merge them."""
    directory = Path(data_dir) if data_dir is not None else default_data_dir()
    audit_path = _resolve(directory, "ALCOHOL_AUDIT_CIWA_FILE", "*Audit_CIWA*.xlsx")
    complaint_path = _resolve(directory, "ALCOHOL_AUDIT_COMPLAINT_FILE", "*PresentingComplaint*.xlsx")
    return merge_records(
        _read_sheet(audit_path, AUDIT_SHEET),
        _read_sheet(complaint_path, COMPLAINT_SHEET),
    )
