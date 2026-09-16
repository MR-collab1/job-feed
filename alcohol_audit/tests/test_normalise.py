"""The source workbooks are hand-typed, so normalisation carries the risk."""

from __future__ import annotations

import pytest

from alcohol_audit import normalise as nz


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Yes", nz.YES),
        ("Yes 2 pairs", nz.YES),
        ("Yes- 2 pairs", nz.YES),
        ("yes 2L", nz.YES),
        ("Yes- Diazepam 10 mg", nz.YES),
        ("Diazepam 5 mg", nz.YES),  # the drug name answers the question
        ("12:08- 1 hour after attendance", nz.YES),  # so does the time it was given
        ("No", nz.NO),
        ("No- AMHAT", nz.NO),  # a qualified no is still a no
        ("-", nz.UNKNOWN),
        ("N/A", nz.UNKNOWN),
        ("", nz.UNKNOWN),
        (None, nz.UNKNOWN),
        ("?", nz.UNKNOWN),
        ("NO NOTES", nz.UNKNOWN),  # missing notes is not a clinical "no"
        ("case Notes missing", nz.UNKNOWN),
    ],
)
def test_tri_state(raw, expected):
    assert nz.tri_state(raw) == expected


def test_no_notes_is_not_counted_as_no():
    """A records failure must not be read as evidence the thing did not happen."""
    assert nz.tri_state("No notes") == nz.UNKNOWN
    assert nz.tri_state("No") == nz.NO


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Yes", nz.DOCUMENTED),
        ("Yes - by Alcohol CNS", nz.DOCUMENTED),
        ("Yes- recorded on correct form", nz.DOCUMENTED),
        ("No", nz.NOT_DOCUMENTED),
        ("case Notes missing", nz.NOTES_UNAVAILABLE),
        ("? med notes missing", nz.NOTES_UNAVAILABLE),
        ("NO NOTES", nz.NOTES_UNAVAILABLE),
        ("-", nz.UNKNOWN),
        (None, nz.UNKNOWN),
    ],
)
def test_ciwa_documentation(raw, expected):
    assert nz.ciwa_documentation(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Admitted", nz.ADMITTED),
        ("Admittted", nz.ADMITTED),  # typo in the source
        ("Discharged", nz.DISCHARGED),
        ("Dsicharged", nz.DISCHARGED),  # typo in the source
        ("Discharged - custody", nz.DISCHARGED),
        ("Discharged /lack of documentation", nz.DISCHARGED),
        ("Self discharged", nz.SELF_DISCHARGED),
        ("Self- discharge", nz.SELF_DISCHARGED),
        ("Self Discharged", nz.SELF_DISCHARGED),
        ("RIP", nz.DIED),
        ("", nz.UNKNOWN),
    ],
)
def test_outcome(raw, expected):
    assert nz.outcome(raw) == expected


def test_self_discharge_is_not_folded_into_discharge():
    assert nz.outcome("Self discharged") != nz.outcome("Discharged")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Chlordiazepoxide 40 mg and Pabrinex", ["chlordiazepoxide", "thiamine"]),
        ("Chlordiazpeoxide 40 mg PO", ["chlordiazepoxide"]),  # misspelling
        ("Chloridazepoxide 30 mg PO", ["chlordiazepoxide"]),  # misspelling
        ("Clordiazepoxide 50 mg and Pabrinex", ["chlordiazepoxide", "thiamine"]),
        ("Diazepam 5 mg PO", ["diazepam"]),
        ("Lorazepam 1 mg IV", ["lorazepam"]),
        ("Pabrinex", ["thiamine"]),
        ("No", []),
        ("N/A", []),
    ],
)
def test_find_drugs(raw, expected):
    assert nz.find_drugs(raw) == expected


def test_chlordiazepoxide_is_not_matched_as_diazepam():
    """The two are different drugs; a substring match would conflate them."""
    assert nz.find_drugs("Chlordiazepoxide 30 mg") == ["chlordiazepoxide"]


def test_find_doses_mg():
    assert nz.find_doses_mg("Chlordiazepoxide 20 mg then 30 mg and Pabrinex") == [20.0, 30.0]
    assert nz.find_doses_mg("Pabrinex") == []


@pytest.mark.parametrize(
    "raw,expected",
    [("18.0", 18), ("3", 3), ("28", 28), ("N/A", None), ("-", None), (None, None), ("99", None)],
)
def test_ciwa_score(raw, expected):
    assert nz.ciwa_score(raw) == expected


@pytest.mark.parametrize(
    "score,band",
    [
        (0, "0-8 minimal"),
        (8, "0-8 minimal"),
        (9, "9-14 mild to moderate"),
        (14, "9-14 mild to moderate"),
        (15, "15-19 moderate to severe"),
        (19, "15-19 moderate to severe"),
        (20, "20+ severe"),
        (None, None),
    ],
)
def test_ciwa_band(score, band):
    assert nz.ciwa_band(score) == band


def test_disposal_splits_code_and_label():
    code, label = nz.disposal(
        "01 - Admitted to a Hospital Bed /became a LODGED PATIENT of the same Health Care Provider"
    )
    assert code == "01"
    assert label == "Admitted to a hospital bed"
    assert nz.disposal("")[0] is None


def test_clean_strips_non_breaking_spaces():
    assert nz.clean("Was CIWA score\xa0documented?") == "Was CIWA score documented?"
