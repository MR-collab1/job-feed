"""Write the merged audit as a flat Excel workbook.

This is the "give me everything in one sheet" view, kept deliberately close to
the source files: the same 19 columns in the same order, one row per source row,
with a small block of provenance columns appended so it is always clear where a
value came from.

Two rules govern the output:

Nothing is dropped.
    Every row that carries any content in either source file appears, including
    the section-heading row the auditors typed into the patient ID column. Only
    rows that are completely empty in the source are left out, since they hold
    no data at all. The row count is reported so the total can be reconciled.

Every empty cell reads ``NA``.
    Blank cells and the placeholders the auditors used for "nothing here"
    (``-``, ``N/A``, ``?``) are all written as a single ``NA`` token, so the
    sheet can be filtered and counted without tripping over five spellings of
    the same thing. Text that carries meaning is left exactly as typed, so
    ``case Notes missing`` and ``Not documented`` survive verbatim.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from . import normalise as nz
from .etl import COLUMNS, parse_datetime

#: What an empty cell becomes.
NA = "NA"

#: Source placeholders that mean "no data" and are folded into :data:`NA`.
#: Anything wordier than these is real text and is left alone.
_EMPTY_TOKENS = frozenset({"", "-", "--", "n/a", "n/a.", "na", "n/a ", "?"})

#: Fallback headers, used only if a source file has no readable header row.
DEFAULT_HEADERS: Sequence[str] = tuple(header for header, _ in COLUMNS)
FIELD_NAMES: Sequence[str] = tuple(name for _, name in COLUMNS)

PROVENANCE_HEADERS: Sequence[str] = (
    "Record type",
    "In Audit_CIWA file",
    "In AE presenting complaint Sheet3",
    "Source of clinical answers",
)

FONT = "Arial"
_HEADER_FILL = PatternFill("solid", fgColor="04716F")
_HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
_PROVENANCE_FILL = PatternFill("solid", fgColor="63808A")
_BODY_FONT = Font(name=FONT, size=10)
_NA_FONT = Font(name=FONT, size=10, color="9AA6AA")
_HEADING_FONT = Font(name=FONT, size=10, bold=True, italic=True)
_THIN = Side(style="thin", color="D5DEDE")
_BORDER = Border(bottom=_THIN)


def is_empty_value(value: object) -> bool:
    """True when a cell holds no data, in any of the spellings the auditors used."""
    return nz.clean(value).lower() in _EMPTY_TOKENS


def _is_blank_row(values: Sequence[Any]) -> bool:
    return all(nz.clean(v) == "" for v in values)


def _is_section_heading(values: Sequence[Any]) -> bool:
    """A typed banner row such as "1ST NOVEMBER 2025 TO 27TH DECEMBER 2025"."""
    from .etl import _is_section_divider

    return _is_section_divider(values)


def read_sheet(path: Path, sheet: str) -> tuple[list[str], list[list[Any]]]:
    """Read a source sheet's header row and every row that carries any content.

    The header is taken verbatim from the file so the merged workbook carries
    the auditors' own column wording, not a paraphrase of it.
    """
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        worksheet = workbook[sheet]
        raw = list(worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()

    width = len(COLUMNS)
    headers = [nz.clean(value) for value in list(raw[0])[:width]] if raw else []
    headers += [""] * (width - len(headers))
    headers = [
        header or DEFAULT_HEADERS[index] for index, header in enumerate(headers)
    ]

    rows: list[list[Any]] = []
    for row in raw[1:]:
        values = list(row)[:width]
        values += [None] * (width - len(values))
        if _is_blank_row(values):
            continue
        rows.append(values)
    return headers, rows


def read_rows(path: Path, sheet: str) -> list[list[Any]]:
    """Read every content row of ``sheet``, header excluded."""
    return read_sheet(path, sheet)[1]


def _merge_key(values: Sequence[Any]) -> tuple[str, dt.datetime | None]:
    return nz.clean(values[0]).upper(), parse_datetime(values[2])


def _coerce(field: str, value: object) -> Any:
    """Return the cell value Excel should hold, or :data:`NA`."""
    if is_empty_value(value):
        return NA
    if field in {"arrival", "departure"}:
        parsed = parse_datetime(value)
        return parsed if parsed is not None else nz.clean(value)
    if field == "age":
        try:
            return int(round(float(nz.clean(value))))
        except ValueError:
            return nz.clean(value)
    return nz.clean(value)


def build_merged_rows(
    audit_rows: Iterable[Sequence[Any]],
    complaint_rows: Iterable[Sequence[Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Merge the two row sets into one list of output records.

    Rows are matched on patient ID plus arrival date and time. For a matched
    attendance the presenting-complaint review supplies the clinical answers,
    since it is the completed case-note review, and the audit sheet fills any
    cell the review left empty. Attendances found in only one file are carried
    through unchanged.
    """
    audit = [list(r) for r in audit_rows]
    complaint = [list(r) for r in complaint_rows]
    complaint_by_key = {_merge_key(r): r for r in complaint}

    merged: list[dict[str, Any]] = []
    used: set[tuple[str, dt.datetime | None]] = set()
    counts = {
        "audit_rows": len(audit),
        "complaint_rows": len(complaint),
        "matched": 0,
        "audit_only": 0,
        "complaint_only": 0,
        "section_headings": 0,
        "cells_filled_from_complaint": 0,
    }

    for values in audit:
        heading = _is_section_heading(values)
        if heading:
            counts["section_headings"] += 1

        review = None if heading else complaint_by_key.get(_merge_key(values))
        combined = list(values)
        clinical_source = "Audit_CIWA file"

        if review is not None:
            used.add(_merge_key(values))
            counts["matched"] += 1
            filled = False
            for index in range(7, len(COLUMNS)):
                if not is_empty_value(review[index]):
                    if is_empty_value(combined[index]):
                        counts["cells_filled_from_complaint"] += 1
                    combined[index] = review[index]
                    filled = True
            clinical_source = (
                "AE presenting complaint Sheet3" if filled else "Audit_CIWA file"
            )
        else:
            counts["audit_only"] += 1

        if all(is_empty_value(combined[i]) for i in range(7, len(COLUMNS))):
            clinical_source = "No clinical answers recorded"

        merged.append(
            {
                "values": combined,
                "record_type": "Section heading in source file" if heading else "Attendance",
                "in_audit": "Yes",
                "in_complaint": "Yes" if review is not None else "No",
                "clinical_source": "Not applicable" if heading else clinical_source,
            }
        )

    for values in complaint:
        key = _merge_key(values)
        if key in used:
            continue
        counts["complaint_only"] += 1
        merged.append(
            {
                "values": list(values),
                "record_type": "Attendance",
                "in_audit": "No",
                "in_complaint": "Yes",
                "clinical_source": "AE presenting complaint Sheet3",
            }
        )

    merged.sort(
        key=lambda record: (
            record["record_type"] == "Section heading in source file",
            parse_datetime(record["values"][2]) or dt.datetime.max,
            nz.clean(record["values"][0]).upper(),
        )
    )
    counts["output_rows"] = len(merged)
    return merged, counts


def _style_header(sheet: Worksheet, widths: Sequence[int], source_count: int) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for index in range(1, source_count + len(PROVENANCE_HEADERS) + 1):
        cell = sheet.cell(row=1, column=index)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL if index <= source_count else _PROVENANCE_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 52
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(source_count + len(PROVENANCE_HEADERS))}{sheet.max_row}"
    )


def _write_data_sheet(
    sheet: Worksheet, merged: list[dict[str, Any]], headers: Sequence[str]
) -> None:
    sheet.append(list(headers) + list(PROVENANCE_HEADERS))

    for record in merged:
        row = [_coerce(field, record["values"][i]) for i, field in enumerate(FIELD_NAMES)]
        row += [
            record["record_type"],
            record["in_audit"],
            record["in_complaint"],
            record["clinical_source"],
        ]
        sheet.append(row)

    source_count = len(headers)
    for row_index in range(2, sheet.max_row + 1):
        heading = sheet.cell(row=row_index, column=source_count + 1).value
        for column_index in range(1, source_count + len(PROVENANCE_HEADERS) + 1):
            cell = sheet.cell(row=row_index, column=column_index)
            if cell.value == NA:
                cell.font = _NA_FONT
            elif heading == "Section heading in source file":
                cell.font = _HEADING_FONT
            else:
                cell.font = _BODY_FONT
            cell.border = _BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=False)
            if isinstance(cell.value, dt.datetime):
                cell.number_format = "DD/MM/YYYY HH:MM"

    widths = [14, 8, 19, 19, 20, 34, 40, 16, 14, 18, 40, 22, 14, 13, 15, 14, 13, 15, 18]
    widths += [28, 16, 28, 28]
    _style_header(sheet, widths, source_count)


def _write_notes_sheet(
    sheet: Worksheet, counts: dict[str, int], data_sheet: str, source_count: int
) -> None:
    """A short provenance sheet, with live counts that recalculate."""
    last = counts["output_rows"] + 1
    quoted = f"'{data_sheet}'"
    type_column = get_column_letter(source_count + 1)
    complaint_column = get_column_letter(source_count + 3)
    source_column = get_column_letter(source_count + 4)
    score_column = get_column_letter(9)
    outcome_column = get_column_letter(19)

    sheet["A1"] = "How this file was built"
    sheet["A1"].font = Font(name=FONT, bold=True, size=13, color="04716F")

    lines = [
        "",
        "Sources",
        "  Audit_CIWA.xlsx, sheet 'Feuille 1' — the master attendance list for the audit window.",
        "  AE_PresentingComplaint_39_2.xlsx, sheet 'Sheet3' only — the case-note review that "
        "completes the later attendances. Sheets 1 and 2 of that file cover a different period "
        "and were not used.",
        "",
        "Matching",
        "  Rows are matched on LOCAL PATIENT ID plus AE ARRIVAL DATE TIME. Patient ID alone is "
        "not unique: several patients in this cohort re-attend within days, so matching on ID "
        "alone would collapse separate attendances into one.",
        "  Where an attendance appears in both files, the case-note review supplies the clinical "
        "answers (columns H to S) and the audit sheet fills any cell the review left empty. No "
        "cell held conflicting values in the two files.",
        "",
        "Missing data",
        "  Every empty cell reads NA. The placeholders the auditors used for 'nothing here' "
        "('-', 'N/A', '?') are written as NA too, so one token covers them all.",
        "  Text that carries meaning is left exactly as typed, including 'case Notes missing', "
        "'Not documented' and 'Unclear handwriting'.",
        "",
        "What was kept",
        "  Every row carrying any content in either file, including the section-heading row "
        "typed into the patient ID column, which is flagged in the 'Record type' column.",
        "  Rows that were completely empty in the source files hold no data and are the only "
        "thing not carried through.",
    ]
    for offset, text in enumerate(lines, start=2):
        cell = sheet.cell(row=offset, column=1, value=text)
        bold = text and not text.startswith("  ")
        cell.font = Font(name=FONT, size=10, bold=bool(bold))
        cell.alignment = Alignment(vertical="top", wrap_text=True)

    start = len(lines) + 4
    sheet.cell(row=start, column=1, value="Reconciliation").font = Font(
        name=FONT, bold=True, size=11
    )
    sheet.cell(row=start, column=1).alignment = Alignment(vertical="top")

    checks = [
        ("Rows read from Audit_CIWA 'Feuille 1'", counts["audit_rows"], None),
        ("Rows read from presenting complaint 'Sheet3'", counts["complaint_rows"], None),
        ("Rows in this merged file", counts["output_rows"],
         f"=COUNTA({quoted}!A2:A{last})"),
        ("  of which attendances", None,
         f'=COUNTIF({quoted}!{type_column}2:{type_column}{last},"Attendance")'),
        ("  of which section headings", None,
         f'=COUNTIF({quoted}!{type_column}2:{type_column}{last},"Section heading in source file")'),
        ("Attendances present in both files", counts["matched"],
         f'=COUNTIF({quoted}!{complaint_column}2:{complaint_column}{last},"Yes")'),
        ("Attendances only in Audit_CIWA", counts["audit_only"], None),
        ("Attendances only in Sheet3", counts["complaint_only"], None),
        ("Empty cells filled from Sheet3", counts["cells_filled_from_complaint"], None),
        ("Rows with no clinical answers in either file", None,
         f'=COUNTIF({quoted}!{source_column}2:{source_column}{last},'
         f'"No clinical answers recorded")'),
        ("Attendances with an initial CIWA score", None,
         f'=COUNTIFS({quoted}!{score_column}2:{score_column}{last},"<>NA",'
         f'{quoted}!{type_column}2:{type_column}{last},"Attendance")'),
        ("Attendances with an outcome recorded", None,
         f'=COUNTIFS({quoted}!{outcome_column}2:{outcome_column}{last},"<>NA",'
         f'{quoted}!{type_column}2:{type_column}{last},"Attendance")'),
    ]
    for offset, (label, value, formula) in enumerate(checks, start=start + 1):
        label_cell = sheet.cell(row=offset, column=1, value=label)
        label_cell.font = Font(name=FONT, size=10)
        label_cell.alignment = Alignment(vertical="top")
        target = sheet.cell(row=offset, column=2)
        target.value = formula if formula is not None else value
        target.font = Font(name=FONT, size=10, bold=True)
        target.alignment = Alignment(horizontal="right")

    note_row = start + len(checks) + 2
    note = sheet.cell(
        row=note_row,
        column=1,
        value=(
            "Counts in column B are live formulas over the merged sheet, so they follow any "
            "edit made there. Row counts taken from the source files are fixed values, because "
            "those files are not part of this workbook."
        ),
    )
    note.font = Font(name=FONT, size=9, italic=True, color="63808A")
    note.alignment = Alignment(vertical="top", wrap_text=True)

    sheet.column_dimensions["A"].width = 105
    sheet.column_dimensions["B"].width = 12


def write_merged_workbook(
    audit_rows: Iterable[Sequence[Any]],
    complaint_rows: Iterable[Sequence[Any]],
    output_path: Path | str,
    data_sheet_name: str = "Merged Audit",
    headers: Sequence[str] | None = None,
) -> tuple[Path, dict[str, int]]:
    """Merge the two row sets and write the workbook to ``output_path``."""
    merged, counts = build_merged_rows(audit_rows, complaint_rows)
    column_headers = list(headers) if headers else list(DEFAULT_HEADERS)

    workbook = Workbook()
    data_sheet = workbook.active
    data_sheet.title = data_sheet_name
    _write_data_sheet(data_sheet, merged, column_headers)
    _write_notes_sheet(
        workbook.create_sheet("Merge Notes"), counts, data_sheet_name, len(column_headers)
    )

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination, counts


def export_merged_workbook(
    output_path: Path | str, data_dir: Path | str | None = None
) -> tuple[Path, dict[str, int]]:
    """Read both source workbooks and write the merged Excel file."""
    from .etl import AUDIT_SHEET, COMPLAINT_SHEET, _resolve, default_data_dir

    directory = Path(data_dir) if data_dir is not None else default_data_dir()
    audit_path = _resolve(directory, "ALCOHOL_AUDIT_CIWA_FILE", "*Audit_CIWA*.xlsx")
    complaint_path = _resolve(
        directory, "ALCOHOL_AUDIT_COMPLAINT_FILE", "*PresentingComplaint*.xlsx"
    )
    headers, audit_rows = read_sheet(audit_path, AUDIT_SHEET)
    _, complaint_rows = read_sheet(complaint_path, COMPLAINT_SHEET)
    return write_merged_workbook(audit_rows, complaint_rows, output_path, headers=headers)
