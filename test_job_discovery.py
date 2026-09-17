from datetime import UTC, datetime, timedelta

from historic_poc import Discovery
from job_discovery import discover_for_resume
from matcher import evaluate_vacancies
from models import EvidenceUnit, ResumeRecord


def _resume(text="Program Analyst performing budget formulation and financial analysis"):
    unit = EvidenceUnit(text, "experience", 1, duration_months=24)
    return ResumeRecord("f", "owner", "resume.pdf", "application/pdf", 100, "", "hash", text, (unit,))


def _summary(control, status="Accepting applications"):
    today = datetime.now(UTC).date()
    return {
        "usajobsControlNumber": control,
        "positionTitle": "Program Analyst",
        "hiringAgencyName": "Test Agency",
        "positionOpenDate": (today - timedelta(days=1)).isoformat(),
        "positionCloseDate": (today + timedelta(days=10)).isoformat(),
        "positionExpireDate": None,
        "positionOpeningStatus": status,
        "payScale": "GS", "minimumGrade": "11",
        "whoMayApply": "The public",
        "positionlocations": [{"positionLocationCity": "Biloxi", "positionLocationState": "Mississippi"}],
    }


class Provider:
    def __init__(self, rows, complete=True):
        self.rows = rows
        self.complete = complete
        self.requested = []

    def discover(self, series):
        assert series == "0343"
        return Discovery((series,), tuple(self.rows), self.complete, 1)

    def announcement(self, control_number):
        self.requested.append(control_number)
        return {
            "usajobsControlNumber": int(control_number),
            "requirementsQualifications": "You must have one year of specialized experience performing budget formulation and financial analysis.",
            "requirementsEducation": "",
            "requirementsConditionsOfEmployment": "",
        }

    def normalize(self, summary, announcement):
        from historic_poc import normalize_pair
        return normalize_pair(summary, announcement)


def test_discovery_to_existing_matcher_and_metadata():
    provider = Provider([_summary(1001), _summary(1002, "Job closed")])
    result = discover_for_resume(_resume(), provider)
    assert result.series == ("0343",)
    assert provider.requested == ["1001"]
    assert len(result.vacancies) == 1
    assert result.vacancies[0]["grades"] == ["GS-11"]
    assert result.vacancies[0]["location"] == "Biloxi, Mississippi"
    batch = evaluate_vacancies(_resume(), list(result.vacancies))
    assert len(batch.decisions) == 1
    assert batch.decisions[0][1].final_outcome == "MATCH"
    assert batch.decisions[0][1].paths[0][0].citations


def test_discovery_without_confident_series_uses_manual_fallback():
    result = discover_for_resume(_resume("General clerical support"))
    assert not result.series and not result.vacancies
    assert "manual announcement" in result.notices[0]


def test_discovery_surfaces_partial_and_text_failure():
    class FailedProvider(Provider):
        def announcement(self, control_number):
            raise ValueError("bad record")
    result = discover_for_resume(_resume(), FailedProvider([_summary(1001)], complete=False))
    assert not result.complete
    assert not result.vacancies
    assert len(result.errors) == 1
    assert any("partially" in message for message in result.notices)
