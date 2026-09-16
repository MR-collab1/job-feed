"""Value normalisation for the A&E alcohol-withdrawal (CIWA) audit.

The two source workbooks were typed by hand over several months, so the same
clinical fact appears in many spellings: ``Yes 2 pairs``, ``yes- 2 pairs``,
``Yes- Diazepam 10 mg``, ``Dsicharged``, ``Self- discharge``.  Every raw value
funnels through this module so the analytics layer only ever sees a small,
closed vocabulary.

Two distinct kinds of "no value" are kept apart on purpose:

``NO``
    The auditor positively recorded that the thing did not happen.
``UNKNOWN``
    Nothing was recorded, or the notes could not be found.  In the first
    workbook this is written ``-``; in the second it is ``N/A``.

Collapsing those two would overstate how much of the pathway was genuinely
assessed, which is the whole point of the audit.
"""

from __future__ import annotations

import re
from typing import Final

YES: Final = "yes"
NO: Final = "no"
UNKNOWN: Final = "unknown"

#: Tokens that mean "nothing was recorded here".
_BLANK_TOKENS: Final = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "n/a.",
    "?",
    "none",
    "nil",
    "not documented",
    "not recorded",
    "unclear handwriting",
    "unclear",
}

#: Tokens that mean "the record itself was unavailable to the auditor".
_NOTES_UNAVAILABLE_PATTERNS: Final = (
    "notes missing",
    "no notes",
    "missing notes",
    "notes unavailable",
)

_TIME_RE: Final = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
_DOSE_RE: Final = re.compile(r"(\d+(?:\.\d+)?)\s*mg\b", re.IGNORECASE)
_NUMBER_RE: Final = re.compile(r"-?\d+(?:\.\d+)?")

#: Canonical drug name -> spellings seen in the source data.  The auditors
#: misspelled chlordiazepoxide five different ways.
_DRUG_ALIASES: Final = {
    "chlordiazepoxide": (
        "chlordiazepoxide",
        "chlordiazpeoxide",
        "chloridazepoxide",
        "clordiazepoxide",
        "chlordiazepoxie",
        "librium",
    ),
    "diazepam": ("diazepam", "valium"),
    "lorazepam": ("lorazepam", "ativan"),
    "thiamine": ("pabrinex", "pabrnex", "parbrinex", "thiamine", "vitamin b"),
}

#: Which canonical drugs count as benzodiazepines.
BENZODIAZEPINES: Final = frozenset({"chlordiazepoxide", "diazepam", "lorazepam"})

#: Which canonical drugs count as thiamine/vitamin replacement.
THIAMINE_DRUGS: Final = frozenset({"thiamine"})

# Outcome categories.
ADMITTED: Final = "admitted"
DISCHARGED: Final = "discharged"
SELF_DISCHARGED: Final = "self_discharged"
DIED: Final = "died"

# CIWA documentation categories.
DOCUMENTED: Final = "documented"
NOT_DOCUMENTED: Final = "not_documented"
NOTES_UNAVAILABLE: Final = "notes_unavailable"


def clean(value: object) -> str:
    """Return ``value`` as a trimmed string with non-breaking spaces removed."""
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def is_blank(value: object) -> bool:
    """True when the cell carries no information at all."""
    return clean(value).lower().rstrip(".") in _BLANK_TOKENS


def _mentions_notes_unavailable(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _NOTES_UNAVAILABLE_PATTERNS)


def tri_state(value: object) -> str:
    """Normalise a free-text yes/no cell to :data:`YES`, :data:`NO` or :data:`UNKNOWN`.

    Anything that names a drug, a dose or a clock time counts as ``YES``: the
    auditors often answered "was a benzodiazepine given?" by writing the drug
    ("Diazepam 5 mg") or the time it went up ("12:08- 1 hour after attendance").
    """
    text = clean(value)
    if not text or is_blank(text) or _mentions_notes_unavailable(text):
        return UNKNOWN

    lowered = text.lower()
    # "No- AMHAT" and "No notes" both start with "no" but mean different things;
    # the notes check above has already taken the second case out.
    if lowered.startswith("yes") or lowered.startswith("y "):
        return YES
    if lowered in {"y"}:
        return YES
    if lowered.startswith("no") or lowered in {"n"}:
        return NO

    # A named drug, a dose or a time all evidence that the thing happened.
    if find_drugs(text) or _DOSE_RE.search(text) or _TIME_RE.search(text):
        return YES
    return UNKNOWN


def find_drugs(value: object) -> list[str]:
    """Return the canonical drug names mentioned in ``value``, alphabetically."""
    lowered = clean(value).lower()
    if not lowered:
        return []
    found = {
        canonical
        for canonical, aliases in _DRUG_ALIASES.items()
        if any(alias in lowered for alias in aliases)
    }
    return sorted(found)


def find_doses_mg(value: object) -> list[float]:
    """Return every ``N mg`` dose mentioned in ``value``, in order."""
    return [float(match) for match in _DOSE_RE.findall(clean(value))]


def ciwa_documentation(value: object) -> str:
    """Classify the "Was CIWA score documented?" column.

    Returns :data:`DOCUMENTED`, :data:`NOT_DOCUMENTED`, :data:`NOTES_UNAVAILABLE`
    or :data:`UNKNOWN`.  ``NOTES_UNAVAILABLE`` is kept separate because a missing
    set of case notes is a records problem, not a failure to score the patient.
    """
    text = clean(value)
    if not text or is_blank(text):
        return UNKNOWN
    if _mentions_notes_unavailable(text):
        return NOTES_UNAVAILABLE
    lowered = text.lower()
    if lowered.startswith("yes"):
        return DOCUMENTED
    if lowered.startswith("no"):
        return NOT_DOCUMENTED
    return UNKNOWN


def ciwa_score(value: object) -> int | None:
    """Return the initial CIWA-Ar score as an int, or ``None`` when not scored."""
    text = clean(value)
    if not text or is_blank(text):
        return None
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    score = float(match.group())
    if not 0 <= score <= 67:  # CIWA-Ar runs 0-67; anything else is a typo.
        return None
    return int(round(score))


def ciwa_band(score: int | None) -> str | None:
    """Group a CIWA-Ar score into the bands used for symptom-triggered therapy."""
    if score is None:
        return None
    if score <= 8:
        return "0-8 minimal"
    if score <= 14:
        return "9-14 mild to moderate"
    if score <= 19:
        return "15-19 moderate to severe"
    return "20+ severe"


#: Scores at or above this trigger treatment under symptom-triggered protocols.
TREATMENT_THRESHOLD: Final = 8


def repeat_count(value: object) -> int | None:
    """Return the recorded number of repeat CIWA scores, or ``None``."""
    text = clean(value)
    if not text or is_blank(text):
        return None
    match = _NUMBER_RE.search(text)
    return int(round(float(match.group()))) if match else None


def outcome(value: object) -> str:
    """Classify the "Admitted/discharged?" column.

    Handles the typos in the source data (``Admittted``, ``Dsicharged``) and
    keeps self-discharge separate from clinician-led discharge, since leaving
    against advice is the outcome the audit is trying to reduce.
    """
    text = clean(value)
    if not text or is_blank(text):
        return UNKNOWN
    lowered = text.lower()
    if "rip" in lowered.split() or "died" in lowered or "deceased" in lowered:
        return DIED
    if lowered.startswith("self") or "self discharg" in lowered or "self- discharg" in lowered:
        return SELF_DISCHARGED
    if "admit" in lowered or "admitt" in lowered:
        return ADMITTED
    if "discharg" in lowered or "dsicharg" in lowered:
        return DISCHARGED
    return UNKNOWN


#: A&E disposal codes seen in the extract, mapped to a short label.
_DISPOSAL_LABELS: Final = {
    "01": "Admitted to a hospital bed",
    "03": "Discharged, no follow-up required",
    "12": "Left before being seen for treatment",
}


def disposal(value: object) -> tuple[str | None, str]:
    """Split the A&E disposal column into its code and a short label."""
    text = clean(value)
    if not text or is_blank(text):
        return None, "Not recorded"
    match = re.match(r"\s*(\d{2})\s*-\s*(.*)", text)
    if match is None:
        return None, text
    code = match.group(1)
    return code, _DISPOSAL_LABELS.get(code, match.group(2).strip())
