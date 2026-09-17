from datetime import UTC, datetime

import pytest

from match_presentation import classify, rank_decisions
from matcher import evaluate_vacancy
from models import EvidenceUnit, ResumeRecord
from resume_ingest import ResumeValidationError, parse_pasted_resume


def _resume(*units):
    return ResumeRecord("f", "owner", "résumé", "text/plain", 200, datetime.now(UTC).isoformat(), "hash", "\n".join(unit.text for unit in units), tuple(units))


def _vacancy(identifier="1", qualifications="You must have one year of specialized experience performing budget formulation and financial analysis."):
    return {
        "id": identifier, "source_hash": identifier, "url": f"https://www.usajobs.gov/job/{identifier}",
        "title": "Program Analyst", "agency": "Agency", "close_date": "2099-12-31",
        "grades": ["GS-11"], "eligibility": "The public", "qualifications": qualifications,
        "specialized_experience": "", "education": "", "conditions": "",
    }


def test_match_requires_work_and_defensible_duration():
    unit = EvidenceUnit("Performed budget formulation and financial analysis.", "experience", 1, duration_months=24)
    decision = evaluate_vacancy(_resume(unit), _vacancy())
    view = classify(decision)
    assert view.label == "MATCH"
    assert view.evidence
    assert not view.gaps


def test_possible_match_requires_cited_work_with_unknown_duration():
    unit = EvidenceUnit("Performed budget formulation and financial analysis.", "experience", 1)
    decision = evaluate_vacancy(_resume(unit), _vacancy())
    assert decision.final_outcome == "NOT A MATCH"  # Core qualification rule remains conservative.
    view = classify(decision)
    assert view.label == "POSSIBLE MATCH"
    assert view.evidence and view.gaps
    assert "duration" in view.explanation


def test_short_known_duration_and_missing_credential_are_not_possible():
    short = EvidenceUnit("Performed budget formulation and financial analysis.", "experience", 1, duration_months=6)
    assert classify(evaluate_vacancy(_resume(short), _vacancy())).label == "NOT A MATCH"
    license_job = _vacancy("2", "Applicants must hold an active registered nurse license.")
    assert classify(evaluate_vacancy(_resume(short), license_job)).label == "NOT A MATCH"


def test_title_only_is_not_possible_and_results_are_ranked_by_classification():
    title_only = evaluate_vacancy(_resume(EvidenceUnit("Program Analyst", "experience", 1)), _vacancy("1"))
    possible = evaluate_vacancy(_resume(EvidenceUnit("Performed budget formulation and financial analysis.", "experience", 1)), _vacancy("2"))
    matched = evaluate_vacancy(_resume(EvidenceUnit("Performed budget formulation and financial analysis.", "experience", 1, duration_months=24)), _vacancy("3"))
    assert classify(title_only).label == "NOT A MATCH"
    ranked = rank_decisions([(_vacancy("1"), title_only), (_vacancy("2"), possible), (_vacancy("3"), matched)])
    assert [item.label for _, _, item in ranked] == ["MATCH", "POSSIBLE MATCH", "NOT A MATCH"]


def test_pasted_resume_is_parsed_without_upload():
    text = "Experience\nProgram Analyst, January 2022 - Present\nPerformed budget formulation and financial analysis, program evaluation, reporting, and stakeholder briefings every week."
    record = parse_pasted_resume(text, "owner")
    assert record.mime_type == "text/plain"
    assert record.owner_id == "owner"
    assert any(unit.section == "experience" and unit.duration_months for unit in record.evidence)
    with pytest.raises(ResumeValidationError):
        parse_pasted_resume("Program Analyst", "owner")
