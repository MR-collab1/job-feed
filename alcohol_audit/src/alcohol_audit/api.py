"""REST API over the merged A&E alcohol-withdrawal (CIWA) audit.

Run locally with::

    uvicorn alcohol_audit.api:app --reload

Every insight endpoint accepts ``date_from`` and ``date_to`` (inclusive, on the
arrival date) so any figure can be re-cut to a shorter period without reloading
the workbooks.

Patient confidentiality
-----------------------
The attendance-level endpoint pseudonymises the local patient ID by default.
Real IDs are only returned when the caller passes ``include_identifiers=true``
*and* the service was started with ``ALCOHOL_AUDIT_ALLOW_IDENTIFIERS=1``, so a
casually deployed instance cannot leak them.
"""

from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi import Path as PathParam
from pydantic import BaseModel

from . import analytics, normalise as nz
from .deck import build_deck
from .etl import Attendance, Dataset, load_dataset

app = FastAPI(
    title="A&E Alcohol Withdrawal CIWA Audit API",
    version="1.0.0",
    description=(
        "Insights from a merged clinical audit of A&E attendances for alcohol "
        "intoxication and withdrawal: CIWA-Ar documentation, treatment given, "
        "repeat scoring and patient outcomes."
    ),
)


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
    """Load and cache the merged dataset for the life of the process."""
    return load_dataset()


def reload_dataset() -> Dataset:
    get_dataset.cache_clear()
    return get_dataset()


class Filters(BaseModel):
    """Shared query filters applied to every insight endpoint."""

    date_from: dt.date | None = None
    date_to: dt.date | None = None
    outcome: Literal["admitted", "discharged", "self_discharged", "died", "unknown"] | None = None
    ciwa_documented: bool | None = None


def filters(
    date_from: dt.date | None = Query(
        None, description="Include attendances arriving on or after this date."
    ),
    date_to: dt.date | None = Query(
        None, description="Include attendances arriving on or before this date."
    ),
    outcome: Literal["admitted", "discharged", "self_discharged", "died", "unknown"] | None = Query(
        None, description="Restrict to one outcome."
    ),
    ciwa_documented: bool | None = Query(
        None, description="Restrict to attendances with (true) or without (false) a CIWA score."
    ),
) -> Filters:
    return Filters(
        date_from=date_from, date_to=date_to, outcome=outcome, ciwa_documented=ciwa_documented
    )


def select(dataset: Dataset, spec: Filters) -> tuple[Attendance, ...]:
    rows = dataset.filtered(
        date_from=spec.date_from,
        date_to=spec.date_to,
        outcome=spec.outcome,
        ciwa_documented=spec.ciwa_documented,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No attendances match those filters.")
    return rows


def _applied(spec: Filters, rows: tuple[Attendance, ...]) -> dict[str, Any]:
    return {
        "filters": {k: (v.isoformat() if isinstance(v, dt.date) else v)
                    for k, v in spec.model_dump().items() if v is not None},
        "attendances_in_scope": len(rows),
    }


@app.get("/health", tags=["service"], summary="Liveness and dataset fingerprint")
def health() -> dict[str, Any]:
    dataset = get_dataset()
    return {
        "status": "ok",
        "attendances": len(dataset),
        "merge_report": dataset.merge_report.__dict__,
    }


@app.post("/admin/reload", tags=["service"], summary="Re-read the workbooks from disk")
def reload_source() -> dict[str, Any]:
    dataset = reload_dataset()
    return {"status": "reloaded", "attendances": len(dataset)}


@app.get("/api/overview", tags=["insights"], summary="Cohort size, period and demographics")
def overview(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "overview": analytics.overview(rows)}


@app.get("/api/insights/treatment", tags=["insights"],
         summary="How many were treated, with what, and over which period")
def treatment(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "treatment": analytics.treatment(rows)}


@app.get("/api/insights/ciwa/documentation", tags=["insights"],
         summary="How often a CIWA-Ar score was documented")
def ciwa_documentation(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "ciwa_documentation": analytics.ciwa_documentation(rows)}


@app.get("/api/insights/ciwa/scores", tags=["insights"],
         summary="Score distribution with treatment at each score and band")
def ciwa_scores(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "ciwa_scores": analytics.ciwa_scores(rows)}


@app.get("/api/insights/ciwa/scores/{score}", tags=["insights"],
         summary="One score value: how many had it, how many were treated")
def ciwa_score_detail(
    score: int = PathParam(..., ge=0, le=67, description="Initial CIWA-Ar score, 0-67."),
    spec: Filters = Depends(filters),
) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "score_detail": analytics.score_detail(rows, score)}


@app.get("/api/insights/ciwa/repeats", tags=["insights"],
         summary="Whether the score was repeated and for how many patients")
def ciwa_repeats(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "ciwa_repeats": analytics.ciwa_repeats(rows)}


@app.get("/api/insights/outcomes", tags=["insights"],
         summary="Admitted, discharged, self-discharged and died")
def outcomes(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "outcomes": analytics.outcomes(rows)}


@app.get("/api/insights/time-periods", tags=["insights"],
         summary="Activity by month, weekday, hour and length of stay")
def time_periods(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "time_periods": analytics.time_periods(rows)}


@app.get("/api/insights/presenting-complaints", tags=["insights"],
         summary="Presenting complaint themes")
def presenting_complaints(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "presenting_complaints": analytics.presenting_complaints(rows)}


@app.get("/api/insights/data-quality", tags=["insights"],
         summary="Gaps in the audit trail")
def data_quality(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "data_quality": analytics.data_quality(rows)}


@app.get("/api/report", tags=["insights"], summary="Every insight in one payload")
def report(spec: Filters = Depends(filters)) -> dict[str, Any]:
    rows = select(get_dataset(), spec)
    return {**_applied(spec, rows), "report": analytics.full_report(rows)}


def _identifiers_allowed() -> bool:
    return os.environ.get("ALCOHOL_AUDIT_ALLOW_IDENTIFIERS", "").strip() in {"1", "true", "yes"}


def _serialise(attendance: Attendance, include_identifiers: bool) -> dict[str, Any]:
    return {
        "patient": attendance.patient_id if include_identifiers else attendance.pseudonym,
        "age": attendance.age,
        "arrival": attendance.arrival.isoformat() if attendance.arrival else None,
        "departure": attendance.departure.isoformat() if attendance.departure else None,
        "length_of_stay_hours": attendance.length_of_stay_hours,
        "last_location": attendance.last_location,
        "presenting_complaint": attendance.presenting_complaint,
        "disposal": attendance.disposal_label,
        "ciwa_documentation": attendance.ciwa_documentation,
        "ciwa_score": attendance.ciwa_score,
        "ciwa_band": attendance.ciwa_band,
        "ciwa_repeated": attendance.ciwa_repeated,
        "repeat_count": attendance.repeat_count,
        "treated": attendance.pharmacologically_treated,
        "benzodiazepine": attendance.benzodiazepine_treated,
        "thiamine_pabrinex": attendance.thiamine_treated,
        "iv_fluids": attendance.iv_fluids_treated,
        "drugs_given": list(attendance.drugs_given),
        "doses_mg": list(attendance.doses_mg),
        "medical_referral": attendance.medical_referral,
        "outcome": attendance.outcome,
        "clinical_source": attendance.clinical_source,
    }


@app.get("/api/attendances", tags=["records"],
         summary="Attendance-level records, pseudonymised by default")
def attendances(
    spec: Filters = Depends(filters),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_identifiers: bool = Query(
        False,
        description=(
            "Return real local patient IDs. Requires the service to run with "
            "ALCOHOL_AUDIT_ALLOW_IDENTIFIERS=1."
        ),
    ),
) -> dict[str, Any]:
    if include_identifiers and not _identifiers_allowed():
        raise HTTPException(
            status_code=403,
            detail=(
                "Identifiable patient data is disabled on this instance. "
                "Start the service with ALCOHOL_AUDIT_ALLOW_IDENTIFIERS=1 to enable it."
            ),
        )
    rows = select(get_dataset(), spec)
    page = rows[offset : offset + limit]
    return {
        **_applied(spec, rows),
        "limit": limit,
        "offset": offset,
        "pseudonymised": not include_identifiers,
        "results": [_serialise(a, include_identifiers) for a in page],
    }


@app.get("/api/report.pptx", tags=["report"],
         summary="Download the audit slide deck", response_class=Response)
def report_pptx(spec: Filters = Depends(filters)) -> Response:
    """Build the PowerPoint deck for the current filter selection.

    The deck contains aggregate figures only. No patient identifiers are
    written into it.
    """
    rows = select(get_dataset(), spec)
    try:
        path: Path = build_deck(analytics.full_report(rows))
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    filename = f"alcohol-ciwa-audit-{dt.date.today().isoformat()}.pptx"
    return Response(
        content=path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
