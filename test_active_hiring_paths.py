"""Regression tests for live-shaped Historic JOA hiring-path eligibility."""

import json

import pytest

from acquisition import build_manual_vacancy
from historic_poc import normalize_pair
from matcher import evaluate_vacancies
from models import EvidenceUnit, ResumeRecord


PUBLIC = "The public"
FEDERAL = "Federal employees - Competitive service"
QUALIFICATIONS = "You must have one year of specialized experience performing budget formulation and financial analysis."


def _summary(*, who_may_apply=None, hiringpaths=None, include_hiringpaths=True):
    summary = {
        "usajobsControlNumber": 987654321,
        "positionOpenDate": "2026-01-01",
        "positionCloseDate": "2099-12-31",
        "positionOpeningStatus": "Accepting applications",
        "positionTitle": "Budget Analyst",
        "hiringAgencyName": "Test Agency",
        "jobcategories": [{"series": "0560"}],
        "payScale": "GS",
        "minimumGrade": "11",
        "maximumGrade": "11",
        "positionlocations": [{"positionLocationCity": "Chicago", "positionLocationState": "IL"}],
        "whoMayApply": who_may_apply,
    }
    if include_hiringpaths:
        summary["hiringpaths"] = hiringpaths
    return summary


def _vacancy(summary):
    return normalize_pair(summary, {
        "usajobsControlNumber": summary["usajobsControlNumber"],
        "requirementsQualifications": QUALIFICATIONS,
    })


def _resume():
    work = EvidenceUnit(
        "Performed budget formulation and financial analysis.",
        "experience",
        page=1,
        duration_months=24,
    )
    return ResumeRecord(
        "synthetic", "test-owner", "resume.txt", "text/plain", 52,
        "2026-09-17T00:00:00Z", "synthetic-resume-hash", work.text, (work,),
    )


def _evaluate(summary):
    return evaluate_vacancies(_resume(), [_vacancy(summary)])


def test_null_who_may_apply_public_hiring_path_does_not_require_federal_status():
    batch = _evaluate(_summary(hiringpaths=[{"hiringPath": PUBLIC}]))
    assert not batch.errors
    assert len(batch.decisions) == 1
    assert batch.decisions[0][1].final_outcome == "MATCH"


def test_null_who_may_apply_restricted_hiring_path_is_audited_and_blocks_match():
    batch = _evaluate(_summary(hiringpaths=[{"hiringPath": FEDERAL}]))
    assert not batch.errors
    assert len(batch.decisions) == 1
    decision = batch.decisions[0][1]
    assert decision.final_outcome == "NOT A MATCH"
    eligibility = [item for path in decision.paths for item in path if item.requirement.kind == "eligibility"]
    assert eligibility
    assert any(FEDERAL in item.requirement.text and not item.satisfied for item in eligibility)


def test_null_who_may_apply_mixed_paths_use_public_route_and_audit_both_source_paths():
    batch = _evaluate(_summary(hiringpaths=[{"hiringPath": PUBLIC}, {"hiringPath": FEDERAL}]))
    assert not batch.errors
    assert len(batch.decisions) == 1
    decision = batch.decisions[0][1]
    assert decision.final_outcome == "MATCH"
    audit = json.dumps(decision.as_dict())
    assert PUBLIC in audit
    assert FEDERAL in audit


@pytest.mark.parametrize("hiringpaths,include_hiringpaths", [
    (None, False),
    (None, True),
    ([], True),
], ids=["missing", "null", "empty"])
def test_null_who_may_apply_unknown_hiring_paths_are_not_evaluable(hiringpaths, include_hiringpaths):
    batch = _evaluate(_summary(hiringpaths=hiringpaths, include_hiringpaths=include_hiringpaths))
    assert not batch.decisions
    assert len(batch.errors) == 1
    assert batch.errors[0].vacancy_id == "987654321"


@pytest.mark.parametrize("who_may_apply,expected", [
    ("Open to the public", "MATCH"),
    ("Current competitive service federal employees only", "NOT A MATCH"),
])
def test_populated_who_may_apply_preserves_existing_behavior(who_may_apply, expected):
    batch = _evaluate(_summary(who_may_apply=who_may_apply, include_hiringpaths=False))
    assert not batch.errors
    assert len(batch.decisions) == 1
    assert batch.decisions[0][1].final_outcome == expected


@pytest.mark.parametrize("who_may_apply,hiring_path,expected", [
    (FEDERAL, PUBLIC, "MATCH"),
    (PUBLIC, FEDERAL, "NOT A MATCH"),
])
def test_structured_hiring_path_resolves_conflicting_who_may_apply(who_may_apply, hiring_path, expected):
    batch = _evaluate(_summary(who_may_apply=who_may_apply, hiringpaths=[{"hiringPath": hiring_path}]))
    assert not batch.errors
    decision = batch.decisions[0][1]
    assert decision.final_outcome == expected
    assert hiring_path in decision.eligibility_source_paths
    assert decision.eligibility_who_may_apply == who_may_apply


def test_manual_announcement_without_structured_hiring_paths_remains_evaluable():
    announcement = ("Qualifications\nYou must have one year of specialized experience performing "
                    "budget formulation and financial analysis.\n") * 4
    vacancy = build_manual_vacancy("Budget Analyst", "Test Agency", announcement)
    assert "eligibility_source" not in vacancy
    batch = evaluate_vacancies(_resume(), [vacancy])
    assert not batch.errors
    assert batch.decisions[0][1].final_outcome == "MATCH"


def test_restricted_federal_route_requires_status_not_related_duty_words():
    experience = EvidenceUnit("Performed budget formulation and financial analysis.", "experience", duration_months=24)
    incidental = EvidenceUnit("Analyzed competitive service hiring processes for an agency.", "experience")
    resume = ResumeRecord("synthetic", "test-owner", "resume.txt", "text/plain", 100,
                          "2026-09-17T00:00:00Z", "incidental-hash", "", (experience, incidental))
    vacancy = _vacancy(_summary(hiringpaths=[{"hiringPath": FEDERAL}]))
    assert evaluate_vacancies(resume, [vacancy]).decisions[0][1].final_outcome == "NOT A MATCH"


def test_restricted_federal_route_accepts_explicit_status_evidence():
    experience = EvidenceUnit("Performed budget formulation and financial analysis.", "experience", duration_months=24)
    status = EvidenceUnit("Current federal employee in the competitive service.", "experience", page=2)
    resume = ResumeRecord("synthetic", "test-owner", "resume.txt", "text/plain", 100,
                          "2026-09-17T00:00:00Z", "status-hash", "", (experience, status))
    vacancy = _vacancy(_summary(hiringpaths=[{"hiringPath": FEDERAL}]))
    decision = evaluate_vacancies(resume, [vacancy]).decisions[0][1]
    assert decision.final_outcome == "MATCH"
    assert decision.eligibility_routes[0].citations[0].page == 2
