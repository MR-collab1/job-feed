"""Analytics tests: denominators, and the specific questions the audit asks."""

from __future__ import annotations

from alcohol_audit import analytics, normalise as nz


def build(dataset_factory, make_record, rows):
    return dataset_factory([make_record(**row) for row in rows]).attendances


def test_overview_counts_patients_and_attendances(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [
        {"patient_id": "A1", "arrival": "01/12/2025 10:00"},
        {"patient_id": "A1", "arrival": "05/12/2025 10:00"},
        {"patient_id": "B2", "arrival": "10/12/2025 10:00"},
    ])
    overview = analytics.overview(rows)
    assert overview["attendances"] == 3
    assert overview["unique_patients"] == 2
    assert overview["reattenders"]["patients"] == 1
    assert overview["reattenders"]["max_visits_by_one_patient"] == 2


def test_period_reports_the_window(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [
        {"arrival": "31/10/2025 11:13", "patient_id": "A"},
        {"arrival": "26/02/2026 23:15", "patient_id": "B"},
    ])
    period = analytics.period(rows)
    assert period["start"] == "2025-10-31"
    assert period["end"] == "2026-02-26"
    assert period["months"] == ["2025-10", "2026-02"]
    assert period["days"] == 119


def test_treatment_separates_fluids_from_withdrawal_treatment(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [
        {"patient_id": "A", "treatment_raw": "Chlordiazepoxide 40 mg and Pabrinex"},
        {"patient_id": "B", "iv_fluids_raw": "Yes"},
        {"patient_id": "C", "treatment_raw": "No"},
    ])
    treatment = analytics.treatment(rows)
    assert treatment["treated"]["count"] == 1
    assert treatment["any_intervention"]["count"] == 2
    assert treatment["iv_fluids"]["count"] == 1
    assert treatment["treated"]["denominator"] == 3


def test_score_detail_answers_the_per_score_question(dataset_factory, make_record):
    """\"CIWA score 8: how many had it, how many of those were treated?\""""
    rows = build(dataset_factory, make_record, [
        {"patient_id": "A", "ciwa_documented_raw": "Yes", "ciwa_score_raw": "8",
         "treatment_raw": "Chlordiazepoxide 20 mg", "outcome_raw": "Admitted"},
        {"patient_id": "B", "ciwa_documented_raw": "Yes", "ciwa_score_raw": "8",
         "treatment_raw": "No", "outcome_raw": "Self discharged"},
        {"patient_id": "C", "ciwa_documented_raw": "Yes", "ciwa_score_raw": "15",
         "treatment_raw": "Pabrinex"},
    ])
    detail = analytics.score_detail(rows, 8)
    assert detail["attendances"] == 2
    assert detail["treated"]["count"] == 1
    assert detail["treated"]["denominator"] == 2
    assert detail["treated"]["percent"] == 50.0
    assert detail["outcomes"][nz.ADMITTED] == 1
    assert detail["outcomes"][nz.SELF_DISCHARGED] == 1
    assert detail["above_treatment_threshold"] is True


def test_score_detail_for_an_absent_score_is_empty_not_an_error(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [{"patient_id": "A"}])
    detail = analytics.score_detail(rows, 8)
    assert detail["attendances"] == 0
    assert detail["treated"]["percent"] == 0.0


def test_ciwa_scores_are_denominated_on_scored_attendances(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [
        {"patient_id": "A", "ciwa_documented_raw": "Yes", "ciwa_score_raw": "18",
         "treatment_raw": "Chlordiazepoxide 40 mg"},
        {"patient_id": "B", "ciwa_documented_raw": "No"},
        {"patient_id": "C", "ciwa_documented_raw": "No"},
    ])
    scores = analytics.ciwa_scores(rows)
    assert scores["denominator_scored"] == 1
    assert scores["denominator_all_attendances"] == 3
    assert scores["by_score"][0]["score"] == 18
    assert scores["by_score"][0]["treated"] == 1


def test_repeats_are_denominated_on_scored_attendances(dataset_factory, make_record):
    """A repeat score cannot exist without an initial one, so unscored rows are excluded."""
    rows = build(dataset_factory, make_record, [
        {"patient_id": "A", "ciwa_documented_raw": "Yes", "ciwa_score_raw": "22",
         "ciwa_repeated_raw": "Yes", "repeat_count_raw": "2",
         "treatment_raw": "Chlordiazepoxide 40 mg"},
        {"patient_id": "B", "ciwa_documented_raw": "Yes", "ciwa_score_raw": "18",
         "ciwa_repeated_raw": "No"},
        {"patient_id": "C", "ciwa_documented_raw": "No"},
    ])
    repeats = analytics.ciwa_repeats(rows)
    assert repeats["denominator_scored_attendances"] == 2
    assert repeats["repeated"]["count"] == 1
    assert repeats["not_repeated"]["count"] == 1
    assert repeats["patients_with_a_repeat_score"] == 1
    assert repeats["repeat_counts"]["max"] == 2
    assert repeats["repeated_detail"][0]["initial_score"] == 22
    assert "patient_id" not in repeats["repeated_detail"][0]


def test_outcomes_keep_self_discharge_separate(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [
        {"patient_id": "A", "outcome_raw": "Admitted"},
        {"patient_id": "B", "outcome_raw": "Self discharged"},
        {"patient_id": "C", "outcome_raw": "Self- discharge"},
        {"patient_id": "D", "outcome_raw": "Discharged"},
        {"patient_id": "E", "outcome_raw": "RIP"},
    ])
    outcomes = analytics.outcomes(rows)
    assert outcomes["admitted"]["count"] == 1
    assert outcomes["self_discharged"]["count"] == 2
    assert outcomes["discharged"]["count"] == 1
    assert outcomes["died"]["count"] == 1
    assert sum(outcomes[k]["count"] for k in
               ("admitted", "discharged", "self_discharged", "died", "not_recorded")) == 5


def test_percentages_never_divide_by_zero(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [{"patient_id": "A"}])
    report = analytics.full_report(rows)
    assert report["ciwa_repeats"]["repeated"]["percent"] == 0.0
    assert report["ciwa_scores"]["denominator_scored"] == 0


def test_full_report_has_every_section(dataset_factory, make_record):
    rows = build(dataset_factory, make_record, [{"patient_id": "A"}])
    report = analytics.full_report(rows)
    assert set(report) >= {
        "overview", "treatment", "ciwa_documentation", "ciwa_scores",
        "ciwa_repeats", "outcomes", "time_periods", "data_quality",
    }


def test_real_dataset_outcome_counts_add_up(real_dataset):
    rows = real_dataset.attendances
    outcomes = analytics.outcomes(rows)
    total = sum(outcomes["by_outcome"][key]["count"] for key in outcomes["by_outcome"])
    assert total == len(rows)


def test_real_dataset_treated_subsets_are_consistent(real_dataset):
    rows = real_dataset.attendances
    treatment = analytics.treatment(rows)
    assert treatment["treated"]["count"] <= treatment["any_intervention"]["count"]
    assert treatment["benzodiazepine_and_thiamine"]["count"] <= treatment["benzodiazepine"]["count"]
    assert treatment["benzodiazepine_and_thiamine"]["count"] <= treatment["thiamine_pabrinex"]["count"]


def test_real_dataset_repeats_cannot_exceed_scores(real_dataset):
    rows = real_dataset.attendances
    repeats = analytics.ciwa_repeats(rows)
    scores = analytics.ciwa_scores(rows)
    assert repeats["repeated"]["count"] <= scores["denominator_scored"]
    assert repeats["denominator_scored_attendances"] == scores["denominator_scored"]


def test_real_dataset_per_score_totals_match_the_scored_total(real_dataset):
    scores = analytics.ciwa_scores(real_dataset.attendances)
    assert sum(row["attendances"] for row in scores["by_score"]) == scores["denominator_scored"]
    assert sum(row["attendances"] for row in scores["by_band"]) == scores["denominator_scored"]
