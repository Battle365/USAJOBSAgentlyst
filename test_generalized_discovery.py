from datetime import UTC, datetime, timedelta

from historic_poc import Discovery, normalize_pair
from job_discovery import discover_for_profile, grade_eligible
from matcher import evaluate_vacancies
from models import EvidenceUnit, ResumeRecord
from profile_extraction import extract_profile


def _resume():
    units = tuple(EvidenceUnit(f"{title}, 2019-2025. Performed professional duties.", "experience", 1, duration_months=72) for title in ("Registered Nurse", "Accountant", "Civil Engineer"))
    return ResumeRecord("f", "o", "r", "text/plain", 1, "", "hash", "\n".join(unit.text for unit in units), units)


def _row(control, series, *, low="11", high="11", pay="GS", status="Accepting applications"):
    today = datetime.now(UTC).date()
    return {
        "usajobsControlNumber": control,
        "positionTitle": {"0610": "Nurse", "0510": "Accountant", "0810": "Civil Engineer"}[series],
        "hiringAgencyName": "Agency", "positionOpenDate": (today - timedelta(days=1)).isoformat(),
        "positionCloseDate": (today + timedelta(days=15)).isoformat(), "positionExpireDate": None,
        "positionOpeningStatus": status, "payScale": pay, "minimumGrade": low, "maximumGrade": high,
        "whoMayApply": "The public", "jobcategories": [{"series": series}],
    }


class Provider:
    def series_catalog(self):
        return {"0610": "Nurse", "0510": "Accounting", "0810": "Civil Engineering"}, True

    def discover(self, series):
        code = {"0610": 610000001, "0510": 510000001, "0810": 810000001}[series]
        return Discovery((series,), (_row(code, series), _row(code + 1, series, low="09", high="09")), True, 1)

    def announcement(self, control_number):
        return {"usajobsControlNumber": int(control_number), "requirementsQualifications": "You must have one year of specialized experience performing professional duties.", "requirementsEducation": "", "requirementsConditionsOfEmployment": ""}

    def normalize(self, summary, text):
        return normalize_pair(summary, text)


def test_default_gs_floor_and_non_gs_policy():
    assert not grade_eligible(_row(1, "0610", low="09", high="09"))
    assert grade_eligible(_row(1, "0610", low="09", high="12"))
    assert grade_eligible(_row(1, "0610", low="02", high="03", pay="VN"))
    assert not grade_eligible(_row(1, "0610", pay=""))
    mixed = normalize_pair(_row(1, "0610", low="09", high="12"), {"usajobsControlNumber": 1, "requirementsQualifications": "Must have experience."})
    assert mixed["grades"] == ["GS-11", "GS-12"]
    assert "GS-11+" in mixed["grade_note"]


def test_three_series_discovery_balances_candidates_and_reuses_matcher():
    provider = Provider()
    catalog, authoritative = provider.series_catalog()
    result = discover_for_profile(extract_profile(_resume(), catalog, authoritative_catalog=authoritative), provider)
    assert set(result.series) == {"0610", "0510", "0810"}
    assert len(result.vacancies) == 3
    assert {job["series"] for job in result.vacancies} == set(result.series)
    assert all(job["grades"] == ["GS-11"] for job in result.vacancies)
    batch = evaluate_vacancies(_resume(), list(result.vacancies))
    assert len(batch.decisions) == 3
