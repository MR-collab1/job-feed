"""Audit insights computed over the merged attendance dataset.

Every function takes a sequence of :class:`~alcohol_audit.etl.Attendance` and
returns plain dictionaries, so the same numbers feed the API responses, the
slide deck and the tests without being recomputed differently in each place.

Denominators are stated explicitly in every result.  The audit has three
different populations and mixing them up changes the answer:

``all attendances``
    Everyone who came through A&E with an alcohol-related complaint.
``case-note reviewed``
    Attendances where an auditor actually got hold of the notes.
``CIWA-scored``
    Attendances with a recorded initial CIWA-Ar score.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

from . import normalise as nz
from .etl import Attendance

Rows = Sequence[Attendance]


def _pct(numerator: int, denominator: int) -> float:
    """Percentage to one decimal place, or 0.0 when the denominator is zero."""
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _counted(numerator: int, denominator: int) -> dict[str, Any]:
    return {"count": numerator, "denominator": denominator, "percent": _pct(numerator, denominator)}


def _summary_stats(values: Iterable[float]) -> dict[str, float | None]:
    data = [v for v in values if v is not None]
    if not data:
        return {"n": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "n": len(data),
        "min": round(min(data), 2),
        "median": round(statistics.median(data), 2),
        "mean": round(statistics.fmean(data), 2),
        "max": round(max(data), 2),
    }


def reviewed(rows: Rows) -> list[Attendance]:
    """Attendances where the case notes were available and reviewed."""
    return [a for a in rows if a.ciwa_documentation != nz.UNKNOWN]


def scored(rows: Rows) -> list[Attendance]:
    """Attendances with a recorded initial CIWA-Ar score."""
    return [a for a in rows if a.ciwa_score is not None]


def period(rows: Rows) -> dict[str, Any]:
    """The time period the cohort covers, derived from arrival dates."""
    arrivals = sorted(a.arrival for a in rows if a.arrival)
    if not arrivals:
        return {"start": None, "end": None, "days": 0, "months": [], "attendances": len(rows)}
    start, end = arrivals[0], arrivals[-1]
    days = (end.date() - start.date()).days + 1
    months = sorted({a.strftime("%Y-%m") for a in arrivals})
    return {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "start_datetime": start.isoformat(),
        "end_datetime": end.isoformat(),
        "days": days,
        "weeks": round(days / 7, 1),
        "months": months,
        "attendances": len(rows),
        "attendances_per_week": round(len(rows) / (days / 7), 1) if days else 0.0,
    }


def overview(rows: Rows) -> dict[str, Any]:
    """Headline cohort description: who, how many, over what period."""
    patients = {a.patient_id for a in rows if a.patient_id}
    visits_per_patient = Counter(a.patient_id for a in rows if a.patient_id)
    reattenders = {pid: n for pid, n in visits_per_patient.items() if n > 1}
    review = reviewed(rows)

    return {
        "attendances": len(rows),
        "unique_patients": len(patients),
        "reattenders": {
            "patients": len(reattenders),
            "attendances": sum(reattenders.values()),
            "percent_of_attendances": _pct(sum(reattenders.values()), len(rows)),
            "max_visits_by_one_patient": max(visits_per_patient.values()) if visits_per_patient else 0,
        },
        "period": period(rows),
        "age_years": _summary_stats(a.age for a in rows),
        "length_of_stay_hours": _summary_stats(a.length_of_stay_hours for a in rows),
        "case_notes_reviewed": _counted(len(review), len(rows)),
        "case_notes_unavailable": _counted(
            sum(1 for a in rows if a.ciwa_documentation == nz.NOTES_UNAVAILABLE), len(rows)
        ),
        "no_audit_record": _counted(
            sum(1 for a in rows if a.ciwa_documentation == nz.UNKNOWN), len(rows)
        ),
    }


def treatment(rows: Rows) -> dict[str, Any]:
    """How many attendances were treated, and with what.

    "Treated" means a benzodiazepine or thiamine replacement was given: the
    drugs that treat alcohol withdrawal.  IV fluids are counted separately as
    supportive care, and an "any intervention" figure includes them.
    """
    total = len(rows)
    treated = [a for a in rows if a.pharmacologically_treated]
    benzo = [a for a in rows if a.benzodiazepine_treated]
    thiamine = [a for a in rows if a.thiamine_treated]
    fluids = [a for a in rows if a.iv_fluids_treated]
    both = [a for a in rows if a.benzodiazepine_treated and a.thiamine_treated]

    drug_counts = Counter()
    for attendance in rows:
        for drug in attendance.drugs_given:
            drug_counts[drug] += 1

    doses = defaultdict(list)
    for attendance in rows:
        if "chlordiazepoxide" in attendance.drugs_given:
            doses["chlordiazepoxide"].extend(attendance.doses_mg)

    return {
        "denominator": total,
        "treated": _counted(len(treated), total),
        "any_intervention": _counted(sum(1 for a in rows if a.any_treatment), total),
        "not_treated": _counted(total - len(treated), total),
        "benzodiazepine": _counted(len(benzo), total),
        "thiamine_pabrinex": _counted(len(thiamine), total),
        "iv_fluids": _counted(len(fluids), total),
        "benzodiazepine_and_thiamine": _counted(len(both), total),
        "drugs_used": [
            {"drug": drug, "attendances": count} for drug, count in drug_counts.most_common()
        ],
        "chlordiazepoxide_dose_mg": _summary_stats(doses["chlordiazepoxide"]),
        "treatment_time_recorded": _counted(
            sum(1 for a in treated if a.treatment_time_recorded), len(treated)
        ),
        "period": period(rows),
        "treated_period": period(treated),
        "monthly": _monthly_treatment(rows),
    }


def _monthly_treatment(rows: Rows) -> list[dict[str, Any]]:
    buckets: dict[str, list[Attendance]] = defaultdict(list)
    for attendance in rows:
        if attendance.month:
            buckets[attendance.month].append(attendance)
    return [
        {
            "month": month,
            "attendances": len(group),
            "treated": sum(1 for a in group if a.pharmacologically_treated),
            "treated_percent": _pct(
                sum(1 for a in group if a.pharmacologically_treated), len(group)
            ),
            "ciwa_scored": sum(1 for a in group if a.ciwa_scored),
            "ciwa_scored_percent": _pct(sum(1 for a in group if a.ciwa_scored), len(group)),
            "admitted": sum(1 for a in group if a.outcome == nz.ADMITTED),
            "self_discharged": sum(1 for a in group if a.outcome == nz.SELF_DISCHARGED),
        }
        for month, group in sorted(buckets.items())
    ]


def ciwa_documentation(rows: Rows) -> dict[str, Any]:
    """How often the CIWA-Ar score was documented at all."""
    total = len(rows)
    review = reviewed(rows)
    categories = Counter(a.ciwa_documentation for a in rows)
    documented = [a for a in rows if a.ciwa_scored]

    return {
        "denominator_all_attendances": total,
        "denominator_case_notes_reviewed": len(review),
        "documented": _counted(len(documented), total),
        "documented_of_reviewed": _counted(
            sum(1 for a in review if a.ciwa_scored), len(review)
        ),
        "not_documented": _counted(categories.get(nz.NOT_DOCUMENTED, 0), total),
        "notes_unavailable": _counted(categories.get(nz.NOTES_UNAVAILABLE, 0), total),
        "no_audit_record": _counted(categories.get(nz.UNKNOWN, 0), total),
        "scoring_time_recorded": _counted(
            sum(1 for a in documented if a.ciwa_scoring_time_recorded), len(documented)
        ),
        "documented_but_no_score_value": _counted(
            sum(
                1
                for a in rows
                if a.ciwa_documentation == nz.DOCUMENTED and a.ciwa_score is None
            ),
            total,
        ),
        "treated_without_any_ciwa_score": _counted(
            sum(1 for a in rows if a.pharmacologically_treated and not a.ciwa_scored),
            sum(1 for a in rows if a.pharmacologically_treated),
        ),
        "monthly": [
            {"month": m["month"], "attendances": m["attendances"], "ciwa_scored": m["ciwa_scored"],
             "percent": m["ciwa_scored_percent"]}
            for m in _monthly_treatment(rows)
        ],
    }


def ciwa_scores(rows: Rows) -> dict[str, Any]:
    """Distribution of initial CIWA-Ar scores, and treatment at each score.

    This answers the "CIWA score 8 — how many had that, and how many of those
    were treated?" question for every score value present, and again grouped
    into the standard severity bands.
    """
    with_score = scored(rows)
    by_score: dict[int, list[Attendance]] = defaultdict(list)
    for attendance in with_score:
        by_score[attendance.ciwa_score].append(attendance)

    per_score = [
        {
            "score": score,
            "band": nz.ciwa_band(score),
            "attendances": len(group),
            "treated": sum(1 for a in group if a.pharmacologically_treated),
            "treated_percent": _pct(
                sum(1 for a in group if a.pharmacologically_treated), len(group)
            ),
            "benzodiazepine": sum(1 for a in group if a.benzodiazepine_treated),
            "thiamine": sum(1 for a in group if a.thiamine_treated),
            "admitted": sum(1 for a in group if a.outcome == nz.ADMITTED),
            "self_discharged": sum(1 for a in group if a.outcome == nz.SELF_DISCHARGED),
            "repeated": sum(1 for a in group if a.ciwa_repeat_performed),
        }
        for score, group in sorted(by_score.items())
    ]

    by_band: dict[str, list[Attendance]] = defaultdict(list)
    for attendance in with_score:
        by_band[attendance.ciwa_band].append(attendance)
    band_order = ["0-8 minimal", "9-14 mild to moderate", "15-19 moderate to severe", "20+ severe"]
    per_band = [
        {
            "band": band,
            "attendances": len(by_band[band]),
            "treated": sum(1 for a in by_band[band] if a.pharmacologically_treated),
            "treated_percent": _pct(
                sum(1 for a in by_band[band] if a.pharmacologically_treated), len(by_band[band])
            ),
            "admitted": sum(1 for a in by_band[band] if a.outcome == nz.ADMITTED),
            "self_discharged": sum(
                1 for a in by_band[band] if a.outcome == nz.SELF_DISCHARGED
            ),
        }
        for band in band_order
        if by_band.get(band)
    ]

    at_or_above = [a for a in with_score if a.ciwa_score >= nz.TREATMENT_THRESHOLD]
    below = [a for a in with_score if a.ciwa_score < nz.TREATMENT_THRESHOLD]

    return {
        "denominator_scored": len(with_score),
        "denominator_all_attendances": len(rows),
        "score_stats": _summary_stats(a.ciwa_score for a in with_score),
        "by_score": per_score,
        "by_band": per_band,
        "treatment_threshold": nz.TREATMENT_THRESHOLD,
        "at_or_above_threshold": {
            **_counted(len(at_or_above), len(with_score)),
            "treated": sum(1 for a in at_or_above if a.pharmacologically_treated),
            "treated_percent": _pct(
                sum(1 for a in at_or_above if a.pharmacologically_treated), len(at_or_above)
            ),
        },
        "below_threshold": {
            **_counted(len(below), len(with_score)),
            "treated": sum(1 for a in below if a.pharmacologically_treated),
            "treated_percent": _pct(
                sum(1 for a in below if a.pharmacologically_treated), len(below)
            ),
        },
    }


def score_detail(rows: Rows, score: int) -> dict[str, Any]:
    """Everything the audit knows about one CIWA-Ar score value."""
    group = [a for a in rows if a.ciwa_score == score]
    treated = [a for a in group if a.pharmacologically_treated]
    outcomes = Counter(a.outcome for a in group)
    return {
        "score": score,
        "band": nz.ciwa_band(score),
        "above_treatment_threshold": score >= nz.TREATMENT_THRESHOLD,
        "attendances": len(group),
        "treated": _counted(len(treated), len(group)),
        "benzodiazepine": _counted(
            sum(1 for a in group if a.benzodiazepine_treated), len(group)
        ),
        "thiamine_pabrinex": _counted(
            sum(1 for a in group if a.thiamine_treated), len(group)
        ),
        "iv_fluids": _counted(sum(1 for a in group if a.iv_fluids_treated), len(group)),
        "repeated": _counted(sum(1 for a in group if a.ciwa_repeat_performed), len(group)),
        "medical_referral": _counted(
            sum(1 for a in group if a.medical_referral == nz.YES), len(group)
        ),
        "outcomes": {key: outcomes.get(key, 0) for key in
                     (nz.ADMITTED, nz.DISCHARGED, nz.SELF_DISCHARGED, nz.DIED, nz.UNKNOWN)},
        "period": period(group),
    }


def ciwa_repeats(rows: Rows) -> dict[str, Any]:
    """Was the CIWA score repeated, and for how many patients?

    Repeat scoring is the part of symptom-triggered treatment that tells you
    whether the patient is responding.  Counted over the attendances that had
    an initial score, since only those can have a repeat.
    """
    with_score = scored(rows)
    repeated = [a for a in with_score if a.ciwa_repeat_performed]
    explicitly_not = [a for a in with_score if a.ciwa_repeated == nz.NO]
    unknown = [a for a in with_score if a.ciwa_repeated == nz.UNKNOWN]
    repeat_patients = {a.patient_id for a in repeated}

    treated_scored = [a for a in with_score if a.pharmacologically_treated]
    treated_repeated = [a for a in treated_scored if a.ciwa_repeat_performed]

    return {
        "denominator_scored_attendances": len(with_score),
        "repeated": _counted(len(repeated), len(with_score)),
        "not_repeated": _counted(len(explicitly_not), len(with_score)),
        "not_recorded": _counted(len(unknown), len(with_score)),
        "patients_with_a_repeat_score": len(repeat_patients),
        "repeat_counts": _summary_stats(
            a.repeat_count for a in repeated if a.repeat_count is not None
        ),
        "treated_and_repeated": _counted(len(treated_repeated), len(treated_scored)),
        "treated_without_repeat": _counted(
            len(treated_scored) - len(treated_repeated), len(treated_scored)
        ),
        "repeated_detail": [
            {
                "pseudonym": a.pseudonym,
                "initial_score": a.ciwa_score,
                "repeat_count": a.repeat_count,
                "treated": a.pharmacologically_treated,
                "outcome": a.outcome,
                "arrival": a.arrival.isoformat() if a.arrival else None,
            }
            for a in repeated
        ],
    }


def outcomes(rows: Rows) -> dict[str, Any]:
    """Admission, discharge, self-discharge and death, with cross-tabs."""
    total = len(rows)
    counts = Counter(a.outcome for a in rows)
    disposal = Counter(a.disposal_label for a in rows)

    def _slice(outcome_key: str) -> list[Attendance]:
        return [a for a in rows if a.outcome == outcome_key]

    by_outcome = {}
    for key in (nz.ADMITTED, nz.DISCHARGED, nz.SELF_DISCHARGED, nz.DIED, nz.UNKNOWN):
        group = _slice(key)
        by_outcome[key] = {
            **_counted(len(group), total),
            "ciwa_scored": sum(1 for a in group if a.ciwa_scored),
            "treated": sum(1 for a in group if a.pharmacologically_treated),
            "treated_percent": _pct(
                sum(1 for a in group if a.pharmacologically_treated), len(group)
            ),
            "median_length_of_stay_hours": _summary_stats(
                a.length_of_stay_hours for a in group
            )["median"],
        }

    self_discharged = _slice(nz.SELF_DISCHARGED)
    return {
        "denominator": total,
        "by_outcome": by_outcome,
        "admitted": _counted(counts.get(nz.ADMITTED, 0), total),
        "discharged": _counted(counts.get(nz.DISCHARGED, 0), total),
        "self_discharged": _counted(counts.get(nz.SELF_DISCHARGED, 0), total),
        "died": _counted(counts.get(nz.DIED, 0), total),
        "not_recorded": _counted(counts.get(nz.UNKNOWN, 0), total),
        "self_discharge_profile": {
            "ciwa_scored": _counted(
                sum(1 for a in self_discharged if a.ciwa_scored), len(self_discharged)
            ),
            "treated": _counted(
                sum(1 for a in self_discharged if a.pharmacologically_treated),
                len(self_discharged),
            ),
            "left_before_being_seen": _counted(
                sum(1 for a in self_discharged if a.disposal_code == "12"), len(self_discharged)
            ),
            "median_length_of_stay_hours": _summary_stats(
                a.length_of_stay_hours for a in self_discharged
            )["median"],
        },
        "ae_disposal": [
            {"disposal": label, "attendances": count, "percent": _pct(count, total)}
            for label, count in disposal.most_common()
        ],
        "medical_referral": _counted(
            sum(1 for a in rows if a.medical_referral == nz.YES), total
        ),
    }


def time_periods(rows: Rows) -> dict[str, Any]:
    """Activity over time: by month, by weekday, by hour, and length of stay."""
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekdays = Counter(
        weekday_names[a.arrival.weekday()] for a in rows if a.arrival
    )
    hours = Counter(a.arrival.hour for a in rows if a.arrival)

    def _hour_block(hour: int) -> str:
        blocks = [(0, 6, "00:00-05:59"), (6, 12, "06:00-11:59"),
                  (12, 18, "12:00-17:59"), (18, 24, "18:00-23:59")]
        for start, end, label in blocks:
            if start <= hour < end:
                return label
        return "unknown"

    blocks = Counter(_hour_block(a.arrival.hour) for a in rows if a.arrival)
    los_values = [a.length_of_stay_hours for a in rows if a.length_of_stay_hours is not None]

    def _los_band(hours_value: float) -> str:
        if hours_value < 4:
            return "under 4h"
        if hours_value < 12:
            return "4-12h"
        if hours_value < 24:
            return "12-24h"
        return "over 24h"

    return {
        "period": period(rows),
        "monthly": _monthly_treatment(rows),
        "by_weekday": [
            {"weekday": name, "attendances": weekdays.get(name, 0)} for name in weekday_names
        ],
        "by_arrival_hour": [
            {"hour": hour, "attendances": hours.get(hour, 0)} for hour in range(24)
        ],
        "by_arrival_block": [
            {"block": label, "attendances": blocks.get(label, 0)}
            for label in ("00:00-05:59", "06:00-11:59", "12:00-17:59", "18:00-23:59")
        ],
        "length_of_stay_hours": _summary_stats(los_values),
        "length_of_stay_bands": [
            {"band": band, "attendances": count}
            for band, count in sorted(
                Counter(_los_band(v) for v in los_values).items(),
                key=lambda item: ["under 4h", "4-12h", "12-24h", "over 24h"].index(item[0]),
            )
        ],
        "four_hour_breaches": _counted(
            sum(1 for v in los_values if v > 4), len(los_values)
        ),
    }


def presenting_complaints(rows: Rows, top: int = 10) -> dict[str, Any]:
    """Group the free-text presenting complaint into broad clinical themes."""
    themes = {
        "Alcohol withdrawal / detox": ("withdraw", "detox", "withdrawel", "withdrawl", "dependant", "alcoholism", "delirium"),
        "Intoxication": ("intoxicat", "intoxication", "alcohol poisoning", "drunk", "reaction to alcohol"),
        "Injury with alcohol": ("injury", "assault", "fall", "lac ", "head", "facial"),
        "Seizure": ("seizure", "siezure", "fit"),
        "Mental health": ("mental health", "suicid", "overdose", "behavioural"),
    }
    counts = Counter()
    for attendance in rows:
        text = attendance.presenting_complaint.lower()
        matched = [name for name, keys in themes.items() if any(k in text for k in keys)]
        if not matched:
            counts["Other alcohol-related"] += 1
        for name in matched:
            counts[name] += 1

    raw = Counter(a.presenting_complaint.strip().lower() for a in rows if a.presenting_complaint)
    return {
        "denominator": len(rows),
        "note": "Themes are keyword-matched and overlap; one complaint can fall in more than one.",
        "themes": [
            {"theme": name, "attendances": count, "percent": _pct(count, len(rows))}
            for name, count in counts.most_common()
        ],
        "most_common_wording": [
            {"complaint": text, "attendances": count} for text, count in raw.most_common(top)
        ],
    }


def data_quality(rows: Rows) -> dict[str, Any]:
    """Where the audit trail breaks down, which is itself a finding."""
    total = len(rows)
    treated = [a for a in rows if a.pharmacologically_treated]
    return {
        "denominator": total,
        "no_audit_record": _counted(
            sum(1 for a in rows if a.ciwa_documentation == nz.UNKNOWN), total
        ),
        "case_notes_unavailable": _counted(
            sum(1 for a in rows if a.ciwa_documentation == nz.NOTES_UNAVAILABLE), total
        ),
        "ciwa_not_documented": _counted(
            sum(1 for a in rows if a.ciwa_documentation == nz.NOT_DOCUMENTED), total
        ),
        "treated_without_ciwa_score": _counted(
            sum(1 for a in treated if not a.ciwa_scored), len(treated)
        ),
        "scored_without_scoring_time": _counted(
            sum(1 for a in scored(rows) if not a.ciwa_scoring_time_recorded), len(scored(rows))
        ),
        "treated_without_treatment_time": _counted(
            sum(1 for a in treated if not a.treatment_time_recorded), len(treated)
        ),
        "outcome_not_recorded": _counted(
            sum(1 for a in rows if a.outcome == nz.UNKNOWN), total
        ),
        "repeat_scoring_not_recorded": _counted(
            sum(1 for a in scored(rows) if a.ciwa_repeated == nz.UNKNOWN), len(scored(rows))
        ),
    }


def full_report(rows: Rows) -> dict[str, Any]:
    """Every insight in one payload, for the deck generator and the API."""
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "overview": overview(rows),
        "treatment": treatment(rows),
        "ciwa_documentation": ciwa_documentation(rows),
        "ciwa_scores": ciwa_scores(rows),
        "ciwa_repeats": ciwa_repeats(rows),
        "outcomes": outcomes(rows),
        "time_periods": time_periods(rows),
        "presenting_complaints": presenting_complaints(rows),
        "data_quality": data_quality(rows),
    }
