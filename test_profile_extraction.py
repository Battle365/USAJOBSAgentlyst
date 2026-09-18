from datetime import UTC, datetime, timedelta

from historic_poc import Discovery, normalize_pair
from job_discovery import discover_for_profile
from models import EvidenceUnit, ResumeRecord
from profile_extraction import DiscoveryConstraints, extract_profile


def _resume(*units):
    evidence = tuple(units)
    return ResumeRecord("f", "owner", "resume.pdf", "application/pdf", 100, "", "resume-hash", "\n".join(unit.text for unit in evidence), evidence)


def test_profile_retains_series_confidence_signals_and_exact_resume_sources():
    role = EvidenceUnit("Registered Nurse, 2019-2025. Provided nursing care.", "experience", 2)
    duties = EvidenceUnit("Performed patient assessment and medication administration.", "experience", 3)
    unrelated = EvidenceUnit("Civil engineering", "skills", 4)
    profile = extract_profile(_resume(role, duties, unrelated), {"0610": "Nurse", "0810": "Civil Engineering"})

    assert profile.resume_hash == "resume-hash"
    assert profile.version == "profile-v1"
    assert [candidate.code for candidate in profile.series_candidates] == ["0610"]
    candidate = profile.series_candidates[0]
    assert candidate.confidence == 80
    assert "documented role: registered nurse" in candidate.occupational_signals
    assert "patient assessment" in candidate.duty_signals
    assert candidate.resume_evidence == (role, duties)
    assert profile.constraints == DiscoveryConstraints()


def test_profile_confidence_rules_and_series_cap_are_unchanged():
    explicit = EvidenceUnit("GS-2210 Information Technology Specialist, 2019-2025.", "experience", 1)
    role = EvidenceUnit("Accountant, 2019-2025. Prepared financial statements.", "experience", 2)
    duty = EvidenceUnit("Performed budget formulation, budget execution, and appropriations analysis.", "experience", 3)
    constraints = DiscoveryConstraints(maximum_series=2)
    profile = extract_profile(
        _resume(explicit, role, duty),
        {"2210": "Information Technology Management", "0510": "Accounting", "0560": "Budget Analysis"},
        constraints=constraints,
    )

    assert [(item.code, item.confidence) for item in profile.series_candidates] == [("2210", 100), ("0510", 80)]
    assert profile.series_candidates[0].resume_evidence == (explicit,)
    assert profile.constraints.maximum_series == 2


def test_discovery_uses_profile_series_grade_floor_and_text_limit():
    role = EvidenceUnit("Registered Nurse, 2019-2025. Provided nursing care.", "experience", 1)
    limits = DiscoveryConstraints(minimum_gs_grade=12, maximum_announcement_texts=1)
    profile = extract_profile(_resume(role), {"0610": "Nurse"}, constraints=limits)
    today = datetime.now(UTC).date()

    def row(control, grade):
        return {
            "usajobsControlNumber": control,
            "positionTitle": "Nurse",
            "hiringAgencyName": "Agency",
            "positionOpenDate": (today - timedelta(days=1)).isoformat(),
            "positionCloseDate": (today + timedelta(days=10)).isoformat(),
            "positionOpeningStatus": "Accepting applications",
            "payScale": "GS",
            "minimumGrade": grade,
            "maximumGrade": grade,
            "whoMayApply": "The public",
            "jobcategories": [{"series": "0610"}],
        }

    class Provider:
        def __init__(self):
            self.searched = []
            self.text_requested = []

        def discover(self, series):
            self.searched.append(series)
            return Discovery((series,), (row(1001, "11"), row(1002, "12"), row(1003, "12")), True, 1)

        def announcement(self, control_number):
            self.text_requested.append(control_number)
            return {"usajobsControlNumber": int(control_number), "requirementsQualifications": "Applicants must have one year of specialized experience providing nursing care."}

        def normalize(self, summary, announcement):
            return normalize_pair(summary, announcement)

    provider = Provider()
    result = discover_for_profile(profile, provider)
    assert provider.searched == ["0610"]
    assert provider.text_requested == ["1002"]
    assert result.open_candidate_count == 2
    assert len(result.vacancies) == 1
    assert result.vacancies[0]["url"] == "https://www.usajobs.gov/job/1002"
    assert any("not exhaustive" in notice for notice in result.notices)
