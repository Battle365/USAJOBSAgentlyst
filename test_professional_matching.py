"""The same qualification rules must work across unrelated professions."""

from datetime import UTC, datetime

import pytest

from matcher import evaluate_vacancy
from models import EvidenceUnit, ResumeRecord


def _resume(*units):
    return ResumeRecord("f", "o", "resume", "text/plain", 1, datetime.now(UTC).isoformat(), "hash", "\n".join(unit.text for unit in units), units)


def _job(title, series, qualifications, *, conditions=""):
    return {
        "id": f"test-{series}", "source_hash": "vacancy-hash", "title": title,
        "series": series, "grades": ["GS-11"], "close_date": "2099-12-31",
        "eligibility": "Open to the public", "qualifications": qualifications,
        "specialized_experience": "", "education": "", "conditions": conditions,
    }


@pytest.mark.parametrize(
    "title,series,requirement,work",
    [
        ("Nurse", "0610", "patient assessment and nursing care", "Provided patient assessment and nursing care"),
        ("Information Technology Specialist", "2210", "network security and systems administration", "Performed network security and systems administration"),
        ("Civil Engineer", "0810", "engineering design and technical specifications", "Performed engineering design and technical specifications"),
        ("Financial Analyst", "0501", "budget formulation and financial reporting", "Performed budget formulation and financial reporting"),
        ("Social Worker", "0185", "psychosocial assessment and case management", "Performed psychosocial assessment and case management"),
    ],
)
def test_performed_work_and_duration_drive_match_across_professions(title, series, requirement, work):
    job = _job(title, series, f"You must have one year of specialized experience performing {requirement}.")
    assert evaluate_vacancy(_resume(EvidenceUnit(f"{title}, GS-11", "experience", 1, duration_months=36)), job).final_outcome == "NOT A MATCH"
    assert evaluate_vacancy(_resume(EvidenceUnit(work, "skills", 1, duration_months=36)), job).final_outcome == "NOT A MATCH"
    assert evaluate_vacancy(_resume(EvidenceUnit(work, "experience", 1, duration_months=6)), job).final_outcome == "NOT A MATCH"
    assert evaluate_vacancy(_resume(EvidenceUnit(work, "experience", 1, duration_months=36)), job).final_outcome == "MATCH"


def test_nursing_license_is_separate_mandatory_gate():
    job = _job("Nurse", "0610", "You must have one year of specialized experience performing patient assessment and nursing care.", conditions="An active registered nurse license is required.")
    work = EvidenceUnit("Provided patient assessment and nursing care", "experience", 1, duration_months=36)
    assert evaluate_vacancy(_resume(work), job).final_outcome == "NOT A MATCH"
    assert evaluate_vacancy(_resume(work, EvidenceUnit("Active registered nurse license", "licenses", 1)), job).final_outcome == "MATCH"
