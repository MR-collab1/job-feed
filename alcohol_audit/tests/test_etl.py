"""The merge is where the two workbooks meet, so it gets the most scrutiny."""

from __future__ import annotations

import datetime as dt

from alcohol_audit import normalise as nz
from alcohol_audit.etl import parse_datetime


def test_parse_datetime_handles_both_source_formats():
    assert parse_datetime("27/12/2025 11:14") == dt.datetime(2025, 12, 27, 11, 14)
    assert parse_datetime(dt.datetime(2025, 12, 27, 11, 14)) == dt.datetime(2025, 12, 27, 11, 14)
    assert parse_datetime("") is None
    assert parse_datetime("not a date") is None


def test_day_first_dates_are_not_read_as_month_first():
    """01/02/2026 is 1 February, not 2 January."""
    assert parse_datetime("01/02/2026 03:24") == dt.datetime(2026, 2, 1, 3, 24)


def test_case_note_review_fills_blank_clinical_fields(make_record, dataset_factory):
    master = make_record()  # clinical columns all blank
    review = make_record(
        ciwa_documented_raw="Yes",
        ciwa_score_raw="17",
        treatment_raw="Chlordiazepoxide 40 mg and Pabrinex",
        outcome_raw="Admitted",
    )
    dataset = dataset_factory([master], [review])

    assert len(dataset) == 1
    attendance = dataset.attendances[0]
    assert attendance.ciwa_score == 17
    assert attendance.outcome == nz.ADMITTED
    assert attendance.clinical_source == "presenting_complaint_sheet3"
    assert dataset.merge_report.matched_attendances == 1
    assert dataset.merge_report.complaint_only_attendances == 0


def test_master_value_survives_a_blank_in_the_review(make_record, dataset_factory):
    """A blank review cell must not wipe a value the master sheet already held."""
    master = make_record(outcome_raw="Admitted", ciwa_score_raw="12", ciwa_documented_raw="Yes")
    review = make_record(outcome_raw=None, ciwa_score_raw="N/A", ciwa_documented_raw="Yes")
    attendance = dataset_factory([master], [review]).attendances[0]

    assert attendance.outcome == nz.ADMITTED
    assert attendance.ciwa_score == 12


def test_same_patient_two_visits_stay_separate(make_record, dataset_factory):
    """Patient ID alone is not the key; several patients re-attend within days."""
    first = make_record(arrival="21/01/2026 16:35", departure="21/01/2026 20:08")
    second = make_record(arrival="21/01/2026 20:57", departure="22/01/2026 06:16")
    review = make_record(arrival="21/01/2026 16:35", departure="21/01/2026 20:08",
                         ciwa_score_raw="3", ciwa_documented_raw="Yes")

    dataset = dataset_factory([first, second], [review])

    assert len(dataset) == 2
    assert dataset.merge_report.matched_attendances == 1
    scored = [a for a in dataset if a.ciwa_score is not None]
    assert len(scored) == 1
    assert scored[0].arrival == dt.datetime(2026, 1, 21, 16, 35)


def test_review_only_attendance_is_kept(make_record, dataset_factory):
    review = make_record(patient_id="RLT9999999", arrival="05/02/2026 09:00")
    dataset = dataset_factory([], [review])

    assert len(dataset) == 1
    assert dataset.merge_report.complaint_only_attendances == 1


def test_section_divider_rows_are_dropped(dataset_factory, make_record):
    """The master sheet carries typed banner rows that are not patients."""
    from alcohol_audit.etl import _is_section_divider, COLUMNS

    width = len(COLUMNS)
    banner = ["1ST NOVEMBER 2025 TO 27TH DECEMBER 2025"] + [None] * (width - 1)
    real = ["RLT1649140", 41, dt.datetime(2025, 12, 27, 11, 14)] + [None] * (width - 3)

    assert _is_section_divider(banner) is True
    assert _is_section_divider(real) is False


def test_attendances_are_sorted_by_arrival(make_record, dataset_factory):
    late = make_record(arrival="26/02/2026 23:15")
    early = make_record(arrival="31/10/2025 11:13")
    dataset = dataset_factory([late, early])
    arrivals = [a.arrival for a in dataset]
    assert arrivals == sorted(arrivals)


def test_length_of_stay_and_month(make_record, dataset_factory):
    attendance = dataset_factory(
        [make_record(arrival="01/12/2025 10:00", departure="01/12/2025 14:30")]
    ).attendances[0]
    assert attendance.length_of_stay_hours == 4.5
    assert attendance.month == "2025-12"


def test_negative_length_of_stay_is_rejected(make_record, dataset_factory):
    attendance = dataset_factory(
        [make_record(arrival="01/12/2025 14:00", departure="01/12/2025 10:00")]
    ).attendances[0]
    assert attendance.length_of_stay_hours is None


def test_pseudonym_is_stable_and_hides_the_id(make_record, dataset_factory):
    rows = dataset_factory([make_record(patient_id="RLT1649140")]).attendances
    pseudonym = rows[0].pseudonym
    assert "RLT1649140" not in pseudonym
    assert pseudonym == dataset_factory(
        [make_record(patient_id="rlt1649140")]
    ).attendances[0].pseudonym


def test_treatment_flags_read_from_either_column(make_record, dataset_factory):
    """The drug is sometimes named in the treatment column and sometimes in the yes/no one."""
    by_column = dataset_factory(
        [make_record(benzodiazepine_raw="Yes", thiamine_raw="Yes 2 pairs")]
    ).attendances[0]
    by_drug_name = dataset_factory(
        [make_record(treatment_raw="Chlordiazepoxide 40 mg and Pabrinex")]
    ).attendances[0]

    for attendance in (by_column, by_drug_name):
        assert attendance.benzodiazepine_treated is True
        assert attendance.thiamine_treated is True
        assert attendance.pharmacologically_treated is True


def test_iv_fluids_alone_is_not_counted_as_treatment(make_record, dataset_factory):
    attendance = dataset_factory([make_record(iv_fluids_raw="Yes")]).attendances[0]
    assert attendance.pharmacologically_treated is False
    assert attendance.any_treatment is True


def test_real_dataset_merges_cleanly(real_dataset):
    report = real_dataset.merge_report
    assert len(real_dataset) == report.merged_rows
    # Every case-note review row should land on an attendance already in the master list.
    assert report.matched_attendances == report.complaint_rows
    assert report.complaint_only_attendances == 0
    assert len({(a.patient_id, a.arrival) for a in real_dataset}) == len(real_dataset)
