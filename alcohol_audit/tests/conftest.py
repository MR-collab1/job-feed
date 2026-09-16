"""Shared fixtures. Adds ``src`` to the path so tests run without installing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alcohol_audit.etl import COLUMNS, merge_records  # noqa: E402

FIELD_NAMES = [name for _, name in COLUMNS]


def record(**overrides) -> dict:
    """Build one raw audit row, with blank clinical fields by default."""
    base = {name: None for name in FIELD_NAMES}
    base.update(
        {
            "patient_id": "RLT0000001",
            "age": 45,
            "arrival": "01/12/2025 10:00",
            "departure": "01/12/2025 14:00",
            "last_location": "Majors",
            "presenting_complaint": "alcohol withdrawal",
            "disposal_reason": "03 - Discharged - did not require any follow up treatment",
        }
    )
    base.update(overrides)
    return base


@pytest.fixture
def make_record():
    return record


@pytest.fixture
def dataset_factory():
    """Build a Dataset from raw row dicts, as the real loader would."""

    def _factory(audit_rows, complaint_rows=()):
        return merge_records(list(audit_rows), list(complaint_rows))

    return _factory


@pytest.fixture
def real_dataset():
    """The real merged dataset, skipped when the source workbooks are absent."""
    from alcohol_audit.etl import load_dataset

    try:
        return load_dataset()
    except FileNotFoundError as error:
        pytest.skip(f"source workbooks not available: {error}")
